"""
Action executor stage - executes actions based on intent.
"""

from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from app.schemas.pipeline import PipelineContext, IntentType, ConversationState
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
            intent=context.intent.value if context.intent else None,
            action_type=context.action_type
        )
        
        # Check if this is a payment proof submission (special case)
        if context.action_type == "payment_proof_received":
            await self._handle_payment_proof_received(context)
        
        # Route to appropriate action based on intent
        elif context.intent == IntentType.PRODUCT_INQUIRY:
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
        Handle purchase intent - manages payment flow based on configuration.
        
        If QR payment is enabled:
        1. Send QR code to customer
        2. Wait for payment proof (image/document)
        3. Forward proof to supervisor with purchase details
        
        If QR payment is disabled:
        1. Notify supervisor of confirmed sale
        2. Mark lead as converted
        """
        context.action_type = "purchase_intent"
        
        # Check if this is a confirmed purchase
        is_purchase_confirmed = await self._is_purchase_confirmed(context)
        
        if not is_purchase_confirmed:
            # Just qualify the lead, not ready to purchase yet
            context.action_result = {
                "lead_id": context.lead_info.get("id") if context.lead_info else None,
                "action": "qualify_lead",
                "stage": "considering"
            }
            return
        
        # Purchase is confirmed, check payment method
        qr_payment_enabled = await self._is_qr_payment_enabled(context)
        
        if qr_payment_enabled:
            # QR payment flow
            await self._handle_qr_payment_flow(context)
        else:
            # Traditional flow: directly notify supervisor
            lead_id = await self._mark_lead_as_converted(context)
            await self._notify_supervisor_sale_completed(context)

            context.current_state = ConversationState.ORDER_COMPLETED
            
            context.action_result = {
                "lead_id": lead_id,
                "action": "sale_completed",
                "payment_method": "non_qr",
                "notified": True
            }
    
    async def _is_purchase_confirmed(self, context: PipelineContext) -> bool:
        """
        Determine if the customer has confirmed their purchase.
        
        Uses keyword-based detection to identify purchase confirmation.
        Does NOT require products in context since customer may have already
        seen product info in previous messages.
        """
        message_lower = context.message_body.lower()
        
        # Keywords that indicate purchase confirmation
        confirmation_keywords = [
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
            "quiero comprar",
            "voy a comprar",
            "compro el plan",
            "quiero el plan",
        ]
        
        # Check if any confirmation keyword is in the message
        for keyword in confirmation_keywords:
            if keyword in message_lower:
                return True
        
        return False
    
    async def _is_qr_payment_enabled(self, context: PipelineContext) -> bool:
        """Check if QR payment is enabled for this tenant."""
        try:
            from app.core.database import get_session_factory
            from app.models import ConfigurationTenant
            from sqlalchemy import select
            
            session_factory = get_session_factory()
            async with session_factory() as db:
                stmt = (
                    select(ConfigurationTenant)
                    .where(ConfigurationTenant.tenant_id == context.tenant_id)
                    .where(ConfigurationTenant.active == True)
                )
                result = await db.execute(stmt)
                config = result.scalar_one_or_none()
                
                if config:
                    return config.is_qr_payment_enabled()
                
                return False
                
        except Exception as e:
            self.log_error(
                "failed_to_check_qr_payment",
                tenant_id=context.tenant_id,
                error=str(e)
            )
            return False
    
    async def _get_qr_payment_url(self, context: PipelineContext) -> str:
        """Get QR payment URL from configuration."""
        try:
            from app.core.database import get_session_factory
            from app.models import ConfigurationTenant
            from sqlalchemy import select
            
            session_factory = get_session_factory()
            async with session_factory() as db:
                stmt = (
                    select(ConfigurationTenant)
                    .where(ConfigurationTenant.tenant_id == context.tenant_id)
                    .where(ConfigurationTenant.active == True)
                )
                result = await db.execute(stmt)
                config = result.scalar_one_or_none()
                
                if config:
                    return config.get_qr_payment_url()
                
                return ""
                
        except Exception as e:
            self.log_error(
                "failed_to_get_qr_payment_url",
                tenant_id=context.tenant_id,
                error=str(e)
            )
            return ""
    
    async def _handle_qr_payment_flow(self, context: PipelineContext):
        """
        Handle QR payment flow:
        1. Send QR code to customer
        2. Mark action as waiting for payment proof
        3. Response generator will wait for next message (payment proof)
        """
        qr_url = await self._get_qr_payment_url(context)
        
        self.log_info(
            "qr_payment_flow_started",
            tenant_id=context.tenant_id,
            qr_enabled=True,
            qr_url_present=bool(qr_url)
        )
        
        if not qr_url:
            self.log_error(
                "qr_payment_enabled_but_no_url",
                tenant_id=context.tenant_id
            )
            context.action_result = {
                "error": "QR URL not configured",
                "action": "qr_payment_failed"
            }
            return
        
        # Send QR to customer via NotificationService
        try:
            customer_name = context.profile_name or (
                context.lead_info.get("name") if context.lead_info else None
            )
            
            self.log_info(
                "sending_qr_code_to_customer",
                customer=context.sender_phone,
                customer_name=customer_name,
                qr_url=qr_url,
                agent=context.recipient_phone
            )
            
            await NotificationService.send_qr_payment_request(
                customer_phone=context.sender_phone,
                agent_phone=context.recipient_phone,
                qr_image_url=qr_url,
                customer_name=customer_name
            )
            
            context.action_result = {
                "action": "awaiting_payment_proof",
                "qr_sent": True,
                "stage": "payment_pending",
                "qr_url": qr_url
            }
            context.current_state = ConversationState.AWAITING_RECEIPT
            
            self.log_info(
                "qr_payment_flow_initiated",
                customer=context.sender_phone,
                lead_id=context.lead_id,
                qr_url=qr_url
            )
            
        except Exception as e:
            self.log_error(
                "failed_to_send_qr_payment",
                customer=context.sender_phone,
                error=str(e),
                error_type=type(e).__name__
            )
            context.action_result = {
                "action": "qr_payment_failed",
                "error": str(e)
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
            self.log_error("no_lead_to_convert", context=context)
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
            self.log_error(
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
            self.log_error("no_supervisor_for_handoff")
            context.action_result = {"notified": False, "error": "No supervisor configured"}
    
    async def _handle_payment_proof_received(self, context: PipelineContext):
        """
        Handle payment proof submission from customer.
        
        This is called when customer sends image/document as payment proof
        after QR payment request was sent.
        
        Actions:
        1. Mark lead as converted
        2. Forward payment proof to supervisor with purchase details
        """
        context.action_type = "payment_proof_received"
        
        if not context.media_urls or len(context.media_urls) == 0:
            self.log_error(
                "payment_proof_no_media",
                lead_id=context.lead_id
            )
            context.action_result = {
                "error": "No payment proof found",
                "action": "payment_proof_invalid"
            }
            return
        
        try:
            # Mark lead as converted
            lead_id = await self._mark_lead_as_converted(context)
            
            # Get supervisor number
            supervisor_number = context.agent_config.get("integrations", {}).get("supervisor_number")
            
            if supervisor_number:
                customer_name = context.profile_name or (
                    context.lead_info.get("name") if context.lead_info else None
                )
                proof_url = context.media_urls[0]  # Use first media URL
                
                # Forward payment proof to supervisor
                await NotificationService.forward_payment_proof_to_supervisor(
                    supervisor_number=supervisor_number,
                    agent_phone=context.recipient_phone,
                    customer_phone=context.sender_phone,
                    customer_name=customer_name,
                    products=context.relevant_products,
                    proof_media_url=proof_url,
                    lead_info=context.lead_info
                )
                
                context.action_result = {
                    "lead_id": lead_id,
                    "action": "payment_proof_forwarded",
                    "supervisor_notified": True,
                    "proof_media_count": len(context.media_urls)
                }
                context.current_state = ConversationState.ORDER_COMPLETED
                
                self.log_info(
                    "payment_proof_processed",
                    lead_id=lead_id,
                    supervisor=supervisor_number,
                    customer=context.sender_phone
                )
            else:
                self.log_error(
                    "no_supervisor_for_payment_proof",
                    lead_id=lead_id
                )
                context.action_result = {
                    "lead_id": lead_id,
                    "error": "Supervisor not configured",
                    "action": "payment_proof_received_but_not_forwarded"
                }
                
        except Exception as e:
            self.log_error(
                "failed_to_process_payment_proof",
                lead_id=context.lead_id,
                error=str(e)
            )
            context.action_result = {
                "error": str(e),
                "action": "payment_proof_processing_failed"
            }
    
    async def _handle_general_question(self, context: PipelineContext):
        """Handle general question."""
        context.action_type = "general_question"
        context.action_result = {}
