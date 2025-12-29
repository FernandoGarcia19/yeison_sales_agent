"""
Response generator stage - generates and sends WhatsApp response.
"""

from app.schemas.pipeline import PipelineContext, IntentType
from app.services.pipeline.base import BasePipelineStage
from app.integrations.whatsapp.client import send_whatsapp_message


class ResponseGeneratorStage(BasePipelineStage):
    """
    Stage 6: Generate and send response to user.
    
    Uses:
    - Agent configuration (personality, tone)
    - Conversation context
    - Action execution results
    - AI/LLM for natural language generation
    """
    
    async def process(self, context: PipelineContext) -> PipelineContext:
        """Generate and send response."""
        
        self.log_info(
            "generating_response",
            intent=context.intent.value if context.intent else None,
            action_type=context.action_type
        )
        
        # Generate response based on intent and action
        response_text = await self._generate_response(context)
        context.response_text = response_text
        
        # Send response via WhatsApp
        try:
            message_sid = await send_whatsapp_message(
                to=context.sender_phone,
                body=response_text
            )
            
            # Store message_sid in action_result
            if not context.action_result:
                context.action_result = {}
            context.action_result["message_sid"] = message_sid
            
            self.log_info(
                "response_sent",
                message_sid=message_sid,
                to=context.sender_phone
            )
            
            # Save message to conversation history
            await self._save_messages_to_conversation(context)
        
        except Exception as e:
            self.log_error(
                "failed_to_send_response",
                error=str(e),
                to=context.sender_phone
            )
            context.error = f"Failed to send response: {str(e)}"
        
        return context
    
    async def _generate_response(self, context: PipelineContext) -> str:
        """
        Generate response text.
        
        In production, use OpenAI/Anthropic to generate personalized responses.
        For now, use simple template-based responses.
        """
        
        # Simple template-based responses (placeholder)
        if context.intent == IntentType.GREETING:
            return self._greeting_response(context)
        
        elif context.intent == IntentType.PRODUCT_INQUIRY:
            return self._product_inquiry_response(context)
        
        elif context.intent == IntentType.PRICING_QUESTION:
            return self._pricing_response(context)
        
        elif context.intent == IntentType.AVAILABILITY_CHECK:
            return self._availability_response(context)
        
        elif context.intent == IntentType.PURCHASE_INTENT:
            return self._purchase_intent_response(context)
        
        elif context.intent == IntentType.CLOSING:
            return "¡Gracias por contactarnos! ¿En qué más puedo ayudarte?"
        
        else:
            return "Interesante pregunta. Déjame ayudarte con eso. ¿Podrías darme más detalles?"
    
    def _greeting_response(self, context: PipelineContext) -> str:
        """Generate greeting response."""
        agent_config = context.agent_config or {}
        agent_name = agent_config.get("name", "Yeison")
        
        if len(context.conversation_history) == 0:
            return f"¡Hola! Soy {agent_name}, tu asistente de ventas. ¿En qué puedo ayudarte hoy?"
        else:
            return f"¡Hola de nuevo! ¿En qué más puedo ayudarte?"
    
    def _product_inquiry_response(self, context: PipelineContext) -> str:
        """Generate product inquiry response."""
        products = context.relevant_products
        
        if not products:
            return "Actualmente no tenemos productos disponibles. ¿Hay algo específico que estés buscando?"
        
        response = "Tenemos estos productos disponibles:\n\n"
        for product in products[:3]:  # Limit to 3 products
            response += f"• {product['name']} - ${product['price']}\n"
            response += f"  {product['description'][:50]}...\n\n"
        
        response += "¿Te interesa alguno en particular?"
        return response
    
    def _pricing_response(self, context: PipelineContext) -> str:
        """Generate pricing response."""
        products = context.relevant_products
        
        if not products:
            return "¿Qué producto te interesa? Déjame ayudarte con los precios."
        
        response = "Aquí están los precios:\n\n"
        for product in products[:3]:
            response += f"• {product['name']}: ${product['price']}\n"
        
        return response
    
    def _availability_response(self, context: PipelineContext) -> str:
        """Generate availability response."""
        products = context.relevant_products
        
        if not products:
            return "Lo siento, ese producto no está disponible en este momento. ¿Te gustaría ver alternativas?"
        
        available_count = len(products)
        response = f"Sí, tenemos {available_count} producto(s) disponible(s):\n\n"
        
        for product in products[:3]:
            response += f"• {product['name']} - {product['quantity']} unidades disponibles\n"
        
        return response
    
    def _purchase_intent_response(self, context: PipelineContext) -> str:
        """Generate purchase intent response."""
        return "¡Excelente! Me encantaría ayudarte con tu compra. ¿Qué producto te gustaría adquirir?"
    
    async def _save_messages_to_conversation(self, context: PipelineContext):
        """Save incoming and outgoing messages to conversation history."""
        
        from app.core.database import get_session_factory
        from app.models import SalesConversation
        from sqlalchemy import select
        
        session_factory = get_session_factory()
        async with session_factory() as db:
            stmt = select(SalesConversation).where(
                SalesConversation.id == context.conversation_id
            )
            result = await db.execute(stmt)
            conversation = result.scalar_one_or_none()
            
            if conversation:
                # Add user message
                conversation.add_message(
                    role="user",
                    content=context.message_body,
                    message_sid=context.message_sid,
                    intent=context.intent.value if context.intent else None,
                    intent_confidence=context.intent_confidence
                )
                
                # Add assistant response
                conversation.add_message(
                    role="assistant",
                    content=context.response_text,
                    message_sid=context.action_result.get("message_sid") if context.action_result else None,
                    action_type=context.action_type
                )
                
                await db.commit()
                
                self.log_info(
                    "messages_saved_to_conversation",
                    conversation_id=context.conversation_id,
                    total_messages=conversation.message_count
                )
