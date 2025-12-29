"""
Action executor stage - executes actions based on intent.
"""

from app.schemas.pipeline import PipelineContext, IntentType
from app.services.pipeline.base import BasePipelineStage, ActionExecutionError


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
        """Handle purchase intent - may create/update lead."""
        context.action_type = "purchase_intent"
        
        # TODO: Call main backend API to create/update lead
        # For now, just log the intent
        context.action_result = {
            "lead_id": context.lead_info.get("id") if context.lead_info else None,
            "action": "qualify_lead"
        }
    
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
    
    async def _handle_general_question(self, context: PipelineContext):
        """Handle general question."""
        context.action_type = "general_question"
        context.action_result = {}
