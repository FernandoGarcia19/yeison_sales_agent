"""
Intent classification stage - uses AI to classify user intent.
"""

from app.schemas.pipeline import PipelineContext, IntentType
from app.services.pipeline.base import BasePipelineStage, ClassificationError


class ClassificationStage(BasePipelineStage):
    """
    Stage 3: Classify user intent using AI.
    
    Uses OpenAI/Anthropic to classify the user's message into predefined intents:
    - greeting
    - product_inquiry
    - pricing_question
    - availability_check
    - purchase_intent
    - objection
    - complaint
    - support_request
    - closing
    - general_question
    - unknown
    """
    
    async def process(self, context: PipelineContext) -> PipelineContext:
        """Classify the intent of the user's message."""
        
        self.log_info(
            "classifying_intent",
            message_sid=context.message_sid,
            message_preview=context.message_body[:50]
        )
        
        # TODO: Implement AI-based intent classification
        # For now, use simple keyword matching as placeholder
        
        intent, confidence = await self._classify_intent_simple(context.message_body)
        
        context.intent = intent
        context.intent_confidence = confidence
        
        self.log_info(
            "intent_classified",
            message_sid=context.message_sid,
            intent=intent.value,
            confidence=confidence
        )
        
        return context
    
    async def _classify_intent_simple(self, message: str) -> tuple[IntentType, float]:
        """
        Simple keyword-based intent classification (placeholder).
        
        In production, replace this with OpenAI/Anthropic API call.
        """
        
        message_lower = message.lower()
        
        # Greeting patterns
        if any(word in message_lower for word in ["hola", "hello", "hi", "buenos días", "buenas tardes"]):
            return IntentType.GREETING, 0.9
        
        # Product inquiry patterns
        if any(word in message_lower for word in ["producto", "product", "qué tienes", "what do you have", "catálogo", "catalog"]):
            return IntentType.PRODUCT_INQUIRY, 0.8
        
        # Pricing patterns
        if any(word in message_lower for word in ["precio", "price", "cuesta", "cost", "cuánto"]):
            return IntentType.PRICING_QUESTION, 0.85
        
        # Availability patterns
        if any(word in message_lower for word in ["disponible", "available", "stock", "hay", "tienes"]):
            return IntentType.AVAILABILITY_CHECK, 0.8
        
        # Purchase intent patterns
        if any(word in message_lower for word in ["comprar", "buy", "quiero", "want", "pedido", "order"]):
            return IntentType.PURCHASE_INTENT, 0.85
        
        # Closing patterns
        if any(word in message_lower for word in ["gracias", "thanks", "adiós", "bye", "chao"]):
            return IntentType.CLOSING, 0.9
        
        # Default to general question
        return IntentType.GENERAL_QUESTION, 0.5
