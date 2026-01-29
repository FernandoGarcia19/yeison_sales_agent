"""
Action executor stage - executes actions based on intent.
"""

from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from app.schemas.pipeline import PipelineContext, IntentType
from app.services.pipeline.base import BasePipelineStage, ActionExecutionError
from app.services.notification_service import NotificationService
from app.models import Lead
from app.core.database import get_session_factory


class ActionExecutorStage(BasePipelineStage):
    """
    Stage 5: Execute actions based on classified intent.
    
    Actions:
    - Check inventory availability
    - Create/update lead (via main backend API)
    - Schedule appointment (via main backend API)
    - Send product information
    - Log interaction
    """
    
    async def process(self, context: PipelineContext) -> PipelineContext:
        """Execute action based on intent."""
        
        self.log_info(
            "executing_action",
            intent=context.intent.value if context.intent else None
        )
        
        # Route to appropriate action based on intent
        if context.intent == IntentType.PRODUCT_INQUIRY:
            await self._handle_product_inquiry(context)
        
        elif context.intent == IntentType.PRICING_QUESTION:
            await self._handle_pricing_question(context)
        
        elif context.intent == IntentType.AVAILABILITY_CHECK:
            await self._handle_availability_check(context)
        
        elif context.intent == IntentType.PURCHASE_INTENT:
            await self._handle_purchase_intent(context)
        
        elif context.intent == IntentType.GREETING:
            await self._handle_greeting(context)
        
        elif context.intent == IntentType.CLOSING:
            await self._handle_closing(context)
        
        elif context.intent == IntentType.HANDOFF_REQUEST:
            await self._handle_handoff_request(context)
        
        else:
            await self._handle_general_question(context)
        
        self.log_info(
            "action_executed",
            action_type=context.action_type
        )
        
        return context
    
    async def _handle_product_inquiry(self, context: PipelineContext):
        """Handle product inquiry intent."""
        context.action_type = "product_inquiry"
        context.action_result = {
            "products": context.relevant_products
        }
    
    async def _handle_pricing_question(self, context: PipelineContext):
        """Handle pricing question intent."""
        context.action_type = "pricing_inquiry"
        context.action_result = {
            "products": context.relevant_products
        }
    
    async def _handle_availability_check(self, context: PipelineContext):
        """Handle availability check intent."""
        context.action_type = "availability_check"
        context.action_result = {
            "products": context.relevant_products,
            "available_count": len(context.relevant_products)
        }
    
    async def _handle_purchase_intent(self, context: PipelineContext):
        """
        Handle purchase intent - may create/update lead and notify supervisor.
        
        This method:
        1. Checks if purchase intent indicates a completed sale
        2. Updates lead status to 'converted' if sale is complete
        3. Notifies supervisor about the completed sale
        """
        context.action_type = "purchase_intent"
        
        # Check if this is a confirmed purchase (you may want to refine this logic)
        is_sale_complete = await self._is_sale_complete(context)
        
        if is_sale_complete:
            # Mark lead as converted
            lead_id = await self._mark_lead_as_converted(context)
            
            # Notify supervisor about completed sale
            await self._notify_supervisor_sale_completed(context)
            
            context.action_result = {
                "lead_id": lead_id,
                "action": "sale_completed",
                "notified": True
            }
        else:
            # Just qualify the lead
            context.action_result = {
                "lead_id": context.lead_info.get("id") if context.lead_info else None,
                "action": "qualify_lead",
                "notified": False
            }
    
    async def _is_sale_complete(self, context: PipelineContext) -> bool:
        """
        Determine if the purchase intent represents a completed sale.
        
        This can be enhanced with more sophisticated logic:
        - Check if payment confirmation was mentioned
        - Check if customer confirmed the order
        - Look for specific keywords in the message
        """
        message_lower = context.message_body.lower()
        
        # Keywords that indicate a completed sale
        completion_keywords = [
            "confirmo",
            "confirmado",
            "confirmar",
            "compro",
            "comprar",
            "acepto",
            "si, lo quiero",
            "lo quiero",
            "proceder con la compra",
            "realizar el pago",
            "hacer el pago",
            "pagar ahora",
            "enviar",
            "envío",
        ]
        
        # Check if any completion keyword is in the message
        for keyword in completion_keywords:
            if keyword in message_lower:
                # Also check if we have products in context
                if context.relevant_products:
                    return True
        
        return False
    
    async def _mark_lead_as_converted(self, context: PipelineContext) -> Optional[int]:
        """Mark the lead as converted in the database."""
        
        if not context.lead_id:
            self.log_warning("no_lead_to_convert", context=context)
            return None
        
        try:
            session_factory = get_session_factory()
            async with session_factory() as db:
                # Update lead status
                stmt = (
                    update(Lead)
                    .where(Lead.id == context.lead_id)
                    .values(
                        status="converted",
                        converted_at=datetime.utcnow(),
                        last_update=datetime.utcnow()
                    )
                )
                await db.execute(stmt)
                await db.commit()
                
                self.log_info(
                    "lead_converted",
                    lead_id=context.lead_id
                )
                
                return context.lead_id
                
        except Exception as e:
            self.log_error(
                "failed_to_convert_lead",
                lead_id=context.lead_id,
                error=str(e)
            )
            return None
    
    async def _notify_supervisor_sale_completed(self, context: PipelineContext):
        """Send notification to supervisor about completed sale."""
        
        # Get supervisor number from agent config
        supervisor_number = context.agent_config.get("integrations", {}).get("supervisor_number")
        
        if not supervisor_number:
            self.log_warning(
                "no_supervisor_number_configured",
                agent_instance_id=context.agent_instance_id
            )
            return
        
        # Prepare customer information
        customer_name = context.profile_name or context.lead_info.get("name") if context.lead_info else None
        
        # Prepare conversation summary (last few messages)
        conversation_summary = self._create_conversation_summary(context.conversation_history)
        
        # Send notification
        try:
            await NotificationService.notify_sale_completed(
                supervisor_number=supervisor_number,
                agent_phone=context.recipient_phone,
                customer_phone=context.sender_phone,
                customer_name=customer_name,
                products=context.relevant_products,
                lead_info=context.lead_info,
                conversation_summary=conversation_summary
            )
            
            self.log_info(
                "supervisor_notified_sale",
                supervisor=supervisor_number,
                customer=context.sender_phone
            )
            
        except Exception as e:
            self.log_error(
                "failed_to_notify_supervisor",
                error=str(e),
                supervisor=supervisor_number
            )
    
    def _create_conversation_summary(self, conversation_history: list) -> str:
        """Create a brief summary of the conversation."""
        
        if not conversation_history:
            return "Sin historial de conversación"
        
        # Get last 3 messages
        recent_messages = conversation_history[-3:]
        
        summary_parts = []
        for msg in recent_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:100]  # Limit to 100 chars
            summary_parts.append(f"{role}: {content}")
        
        return "\n".join(summary_parts)
    
    async def _handle_greeting(self, context: PipelineContext):
        """Handle greeting intent."""
        context.action_type = "greeting"
        context.action_result = {
            "is_first_message": len(context.conversation_history) == 0
        }
    
    async def _handle_closing(self, context: PipelineContext):
        """Handle closing intent."""
        context.action_type = "closing"
        context.action_result = {}
    
    async def _handle_handoff_request(self, context: PipelineContext):
        """
        Handle handoff request - customer wants to talk to a human.
        Notifies supervisor immediately.
        """
        context.action_type = "handoff_request"
        
        # Get supervisor number from agent config
        supervisor_number = context.agent_config.get("integrations", {}).get("supervisor_number")
        
        if supervisor_number:
            customer_name = context.profile_name or context.lead_info.get("name") if context.lead_info else None
            conversation_context = self._create_conversation_summary(context.conversation_history)
            
            try:
                await NotificationService.notify_handoff_request(
                    supervisor_number=supervisor_number,
                    agent_phone=context.recipient_phone,
                    customer_phone=context.sender_phone,
                    customer_name=customer_name,
                    reason="Cliente solicitó hablar con un humano",
                    conversation_context=conversation_context
                )
                
                self.log_info(
                    "handoff_notification_sent",
                    supervisor=supervisor_number,
                    customer=context.sender_phone
                )
                
                context.action_result = {"notified": True}
                
            except Exception as e:
                self.log_error(
                    "failed_to_notify_handoff",
                    error=str(e)
                )
                context.action_result = {"notified": False, "error": str(e)}
        else:
            self.log_warning("no_supervisor_for_handoff")
            context.action_result = {"notified": False, "error": "No supervisor configured"}
    
    async def _handle_general_question(self, context: PipelineContext):
        """Handle general question."""
        context.action_type = "general_question"
        context.action_result = {}
