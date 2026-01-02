"""
Intent classification stage - uses AI to classify user intent.
"""

import json
from typing import Optional
from openai import AsyncOpenAI

from app.schemas.pipeline import PipelineContext, IntentType
from app.services.pipeline.base import BasePipelineStage, ClassificationError
from app.core.config import settings


class ClassificationStage(BasePipelineStage):
    """
    Stage 3: Classify user intent using AI.
    
    Uses OpenAI/OpenRouter to classify the user's message into predefined intents:
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
    
    def __init__(self):
        super().__init__()
        self._client: Optional[AsyncOpenAI] = None
    
    def _get_client(self) -> AsyncOpenAI:
        """Get or create OpenAI/OpenRouter client."""
        if self._client is None:
            if settings.use_openrouter:
                # Configure for OpenRouter
                self._client = AsyncOpenAI(
                    api_key=settings.openrouter_api_key,
                    base_url="https://openrouter.ai/api/v1"
                )
            else:

                self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._client
    
    async def process(self, context: PipelineContext) -> PipelineContext:
        """Classify the intent of the user's message."""
        
        self.log_info(
            "classifying_intent",
            message_sid=context.message_sid,
            message_preview=context.message_body[:50]
        )
        
        try:
            intent, confidence = await self._classify_intent_ai(
                context.message_body,
                context.conversation_history
            )
        except Exception as e:
            self.log_error(
                "classification_failed",
                error=str(e),
                message_sid=context.message_sid
            )
            # Fallback to unknown intent with low confidence
            intent = IntentType.UNKNOWN
            confidence = 0.3
        
        context.intent = intent
        context.intent_confidence = confidence
        
        self.log_info(
            "intent_classified",
            message_sid=context.message_sid,
            intent=intent.value,
            confidence=confidence
        )
        
        return context
    
    async def _classify_intent_ai(
        self,
        message: str,
        conversation_history: list[dict] = None
    ) -> tuple[IntentType, float]:
        """
        AI-based intent classification using OpenAI/OpenRouter.
        
        Args:
            message: The user's message to classify
            conversation_history: Previous messages for context (optional)
            
        Returns:
            Tuple of (IntentType, confidence_score)
        """
        
        # Build context from conversation history if available
        context_messages = ""
        if conversation_history and len(conversation_history) > 0:
            recent_messages = conversation_history[-3:]  # Last 3 messages for context
            context_messages = "\n".join([
                f"{msg.get('role', 'user')}: {msg.get('content', '')}"
                for msg in recent_messages
            ])
            context_section = f"\n\nContexto de conversación reciente:\n{context_messages}"
        else:
            context_section = ""
        
        # System prompt for intent classification
        system_prompt = """Eres un clasificador de intenciones para un chatbot de ventas en español. Tu tarea es clasificar el mensaje del usuario en una de las siguientes intenciones:

INTENCIONES DISPONIBLES:
- greeting: Saludos, presentaciones iniciales ("hola", "buenos días", "buenas tardes", "qué tal")
- product_inquiry: Consultas sobre productos, catálogo, qué vendes ("qué productos tienes", "qué vendes", "muéstrame tu catálogo")
- pricing_question: Preguntas sobre precios, costos ("cuánto cuesta", "qué precio tiene", "cuál es el costo")
- availability_check: Verificar disponibilidad, stock ("tienes disponible", "hay en stock", "cuándo llega")
- purchase_intent: Intención de compra, hacer un pedido ("quiero comprar", "me interesa", "lo quiero", "hacer un pedido")
- objection: Objeciones, dudas sobre la compra ("es muy caro", "no estoy seguro", "necesito pensarlo")
- complaint: Quejas, reclamos, insatisfacción ("no llegó mi pedido", "está defectuoso", "quiero mi dinero")
- handoff_request: El usuario pide o requiere de la asistencia de un humano 
- closing: Despedidas, fin de conversación ("gracias", "adiós", "hasta luego", "chao")
- general_question: Preguntas generales que no encajan en otras categorías
- unknown: Mensajes confusos o que no se pueden clasificar claramente

Responde ÚNICAMENTE con un objeto JSON en este formato exacto:
{
  "intent": "nombre_de_la_intencion",
  "confidence": 0.95,
}

El campo "confidence" debe ser un número entre 0 y 1, donde:
- 0.9-1.0: Muy seguro de la clasificación
- 0.7-0.89: Razonablemente seguro
- 0.5-0.69: Moderadamente seguro
- 0.3-0.49: Poca certeza
- 0-0.29: Muy incierto

Considera el contexto de la conversación si está disponible."""
        
        # User prompt with the message to classify
        user_prompt = f"""Clasifica la siguiente intención del mensaje del usuario:{context_section}

Mensaje actual del usuario: "{message}"

Responde con el JSON de clasificación."""
        
        client = self._get_client()
        
        # Select model based on configuration
        model = settings.openrouter_model if settings.use_openrouter else settings.openai_model
        
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,  
                max_tokens=200,
                response_format={"type": "json_object"}
            )
            
            # Parse the response
            result_text = response.choices[0].message.content.strip()
            result = json.loads(result_text)
            
            # Extract intent and confidence
            intent_str = result.get("intent", "unknown").lower()
            confidence = float(result.get("confidence", 0.5))
            
            # Validate and convert to IntentType enum
            try:
                intent = IntentType(intent_str)
            except ValueError:
                # If intent string doesn't match enum, default to UNKNOWN
                self.log_warning(
                    "unknown_intent_returned",
                    intent_string=intent_str,
                    defaulting_to="unknown"
                )
                intent = IntentType.UNKNOWN
                confidence = 0.3
            
            return intent, confidence
            
        except json.JSONDecodeError as e:
            self.log_error(
                "json_parse_error",
                error=str(e),
                response_text=result_text if 'result_text' in locals() else "N/A"
            )
            raise ClassificationError(f"Failed to parse AI response: {e}")
        except Exception as e:
            self.log_error(
                "ai_classification_error",
                error=str(e)
            )
            raise ClassificationError(f"AI classification failed: {e}")
