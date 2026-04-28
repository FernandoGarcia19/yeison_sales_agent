"""
Response generator stage - generates and sends WhatsApp response.
"""

import json
from typing import Optional
from openai import AsyncOpenAI

from app.schemas.pipeline import PipelineContext, IntentType
from app.services.pipeline.base import BasePipelineStage
from app.integrations.whatsapp.client import send_whatsapp_message
from app.core.config import settings


class ResponseGeneratorStage(BasePipelineStage):
    """
    Stage 6: Generate and send response to user.
    
    Uses:
    - Agent configuration (personality, tone)
    - Conversation context
    - Action execution results
    - AI/LLM for natural language generation
    """
    
    def __init__(self):
        super().__init__()
        self._client: Optional[AsyncOpenAI] = None
    
    def _get_client(self) -> AsyncOpenAI:
        """Get or create OpenAI/OpenRouter client."""
        if self._client is None:
            if settings.use_openrouter:
                self._client = AsyncOpenAI(
                    api_key=settings.openrouter_api_key,
                    base_url="https://openrouter.ai/api/v1"
                )
            else:
                self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._client
    
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
        
        # Send response via WhatsApp using the agent's phone number (multitenant)
        try:
            message_sid = await send_whatsapp_message(
                to=context.sender_phone,
                body=response_text,
                from_number=context.recipient_phone  # Use agent's phone number for response
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
        Generate AI-powered response text using OpenAI/OpenRouter.
        
        Uses agent configuration, conversation context, and intent to create
        personalized, natural responses in Spanish.
        """
        
        try:
            response = await self._generate_ai_response(context)
            return response
        except Exception as e:
            self.log_error(
                "ai_generation_failed",
                error=str(e),
                falling_back_to="template"
            )
            # Fallback to template-based responses
            return self._generate_fallback_response(context)
    
    async def _generate_ai_response(self, context: PipelineContext) -> str:
        """Generate response using AI/LLM."""
        
        # Build system prompt with agent configuration
        system_prompt = self._build_system_prompt(context)
        
        # Build user prompt with context
        user_prompt = self._build_user_prompt(context)
        
        client = self._get_client()
        model = settings.openrouter_model if settings.use_openrouter else settings.openai_model
        
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=settings.openai_temperature,
            extra_body={"max_completion_tokens": settings.openai_max_tokens}
        )
        
        response_text = response.choices[0].message.content.strip()
        
        # Validate response length
        agent_config = context.agent_config or {}
        max_length = agent_config.get("response_settings", {}).get("max_response_length", 500)
        
        if len(response_text) > max_length:
            response_text = response_text[:max_length-3] + "..."
        
        return response_text
    
    def _build_system_prompt(self, context: PipelineContext) -> str:
        """Build system prompt using agent configuration."""
        
        agent_config = context.agent_config or {}
        
        # Extract configuration
        agent_info = agent_config.get("agent_info", {})
        agent_name = agent_info.get("name", "Asistente de Ventas")
        
        tenant_info = agent_config.get("tenant_info", {})
        company_name = tenant_info.get("company_name", "nuestra empresa")
        industry = tenant_info.get("industry", "ventas")
        description = tenant_info.get("description", "")
        contact_info = tenant_info.get("contact_info", {})
        
        # Log what we're using for the prompt
        self.log_info(
            "building_system_prompt",
            agent_config_keys=list(agent_config.keys()),
            tenant_info_keys=list(tenant_info.keys()),
            company_name=company_name,
            industry=industry,
            has_description=bool(description),
            has_contact=bool(contact_info),
            has_product_info=bool(agent_config.get("product_info")),
            has_operations_info=bool(agent_config.get("operations_info"))
        )
        
        # CRITICAL: Log if we're using defaults
        if company_name == "nuestra empresa":
            self.log_error(
                "using_default_company_name",
                tenant_info=tenant_info,
                full_agent_config_keys=list(agent_config.keys())
            )
        
        personality = agent_config.get("personality", {})
        tone = personality.get("tone", "friendly")
        formality = personality.get("formality_level")
        emoji_usage = personality.get("emoji_usage", "moderate")
        response_length = personality.get("response_length", "concise")
        brand_voice = personality.get("brand_voice", "")
        custom_phrases = personality.get("custom_phrases", {})
        
        response_settings = agent_config.get("response_settings", {})
        include_pricing = response_settings.get("include_pricing", True)
        show_availability = response_settings.get("show_availability", True)
        
        # Build emoji guidance
        emoji_guide = {
            "none": "NUNCA uses emojis.",
            "minimal": "Usa emojis muy ocasionalmente (máximo 1 por mensaje).",
            "moderate": "Usa emojis moderadamente para dar calidez (2-3 por mensaje).",
            "frequent": "Usa emojis frecuentemente para ser expresivo y amigable."
        }.get(emoji_usage, "Usa emojis moderadamente.")
        
        # Build response length guidance
        length_guide = {
            "brief": "Respuestas MUY breves (1-2 líneas máximo).",
            "concise": "Respuestas concisas pero completas (2-4 líneas).",
            "detailed": "Respuestas detalladas y explicativas cuando sea necesario."
        }.get(response_length, "Respuestas concisas.")
        
        # Build tone guidance
        tone_guide = {
            "friendly": "amigable y cercano",
            "professional": "profesional y cortés",
            "casual": "casual y relajado",
            "formal": "formal y respetuoso"
        }.get(tone, "amigable")
        
        # Get additional business context from new config structure
        product_info = agent_config.get("product_info", {})
        operations_info = agent_config.get("operations_info", {})
        
        system_prompt = f"""Eres {agent_name}, un asistente virtual de ventas para {company_name}.

INFORMACIÓN DE LA EMPRESA:
- Nombre: {company_name}
- Industria: {industry}
- Descripción: {description if description else 'Empresa dedicada a ' + industry}
- Ubicación: {tenant_info.get('location', 'N/A')}
- Sitio web: {tenant_info.get('website', 'N/A')}
"""
        
        if contact_info and any(contact_info.values()):
            system_prompt += f"""
INFORMACIÓN DE CONTACTO:"""
            if contact_info.get('name'):
                system_prompt += f"\n- Contacto: {contact_info.get('name')}"
                if contact_info.get('role'):
                    system_prompt += f" ({contact_info.get('role')})"
            if contact_info.get('phone'):
                system_prompt += f"\n- Teléfono: {contact_info.get('phone')}"
            system_prompt += "\n"
        
        # Add product information if available
        if product_info:
            if product_info.get('unique_selling_points'):
                system_prompt += f"""
PROPUESTAS DE VALOR:
{product_info['unique_selling_points']}
"""
            if product_info.get('target_audience'):
                system_prompt += f"""
AUDIENCIA OBJETIVO:
{product_info['target_audience']}
"""
            if product_info.get('payment_methods'):
                system_prompt += f"""
MÉTODOS DE PAGO:
{product_info['payment_methods']}
"""
        
        # Add operations information if available
        if operations_info:
            if operations_info.get('sales_process'):
                system_prompt += f"""
PROCESO DE VENTAS:
{operations_info['sales_process']}
"""
            if operations_info.get('common_questions'):
                system_prompt += f"""
PREGUNTAS FRECUENTES:
{operations_info['common_questions']}
"""
            if operations_info.get('objections'):
                system_prompt += f"""
MANEJO DE OBJECIONES:
{operations_info['objections']}
"""
            if operations_info.get('closing_techniques'):
                system_prompt += f"""
TÉCNICAS DE CIERRE:
{operations_info['closing_techniques']}
"""
        
        if brand_voice:
            system_prompt += f"""
VOZ DE MARCA:
{brand_voice}
"""
        
        # Add custom phrases section
        if custom_phrases:
            system_prompt += """
FRASES PERSONALIZADAS:
"""
            if custom_phrases.get('greeting'):
                system_prompt += f"""- Saludo inicial: "{custom_phrases['greeting']}"
"""
            if custom_phrases.get('farewell'):
                system_prompt += f"""- Despedida: "{custom_phrases['farewell']}"
"""
            if custom_phrases.get('thanks'):
                system_prompt += f"""- Agradecimiento: "{custom_phrases['thanks']}"
"""
        
        system_prompt += f"""
PERSONALIDAD Y ESTILO:
- Tono: {tone_guide}
- Formalidad: {formality}
- Emojis: {emoji_guide}
- Longitud de respuesta: {length_guide}
- SIEMPRE responde en ESPAÑOL

CAPACIDADES:
- Responder preguntas sobre productos y servicios
- Proporcionar información de precios
- Verificar disponibilidad de productos
- Ayudar con el proceso de compra
- Proporcionar información sobre {company_name} (nombre, ubicación, contacto, etc.)

INSTRUCCIONES IMPORTANTES:
1. Si te preguntan sobre la empresa, el negocio, o información de contacto, SIEMPRE usa la información proporcionada arriba
2. Cuando te saluden por primera vez, usa tu frase de saludo personalizada si está configurada
3. Sé {'natural y conversacional' if formality == 'casual' else 'profesional y cortés'}
4. {"Incluye precios cuando sea relevante" if include_pricing else "Evita mencionar precios específicos sin autorización"}
5. {"Menciona disponibilidad cuando se pregunte por productos" if show_availability else "Confirma disponibilidad antes de mencionar stock"}
6. Mantén las respuestas {"breves y directas" if response_length == "brief" else "completas pero concisas" if response_length == "concise" else "detalladas y explicativas"}
7. Si no tienes información suficiente sobre un producto específico, pregunta educadamente para clarificar
8. Si el usuario necesita hablar con una persona, facilita el proceso amablemente
9. NUNCA digas que no conoces el nombre de la empresa o que no tienes información - TODA la información está en este prompt

IMPORTANTE: 
- El nombre de tu empresa es {company_name}
- Tu nombre es {agent_name}
- NO inventes información que no te proporcionen
- Si no hay productos disponibles o información específica sobre UN PRODUCTO, sé honesto
- Pero SIEMPRE conoces la información básica de {company_name} que está arriba
- Siempre busca ayudar al cliente de la mejor manera posible"""

        return system_prompt
    
    def _build_user_prompt(self, context: PipelineContext) -> str:
        """Build user prompt with conversation context."""
        
        prompt = f"""MENSAJE DEL USUARIO: "{context.message_body}"

INTENCIÓN DETECTADA: {context.intent.value if context.intent else 'unknown'}
CONFIANZA: {context.intent_confidence if context.intent_confidence else 0}

"""
        
        # Add conversation history
        if context.conversation_history and len(context.conversation_history) > 0:
            prompt += "HISTORIAL DE CONVERSACIÓN:\n"
            for msg in context.conversation_history[-10:]:  # Last 10 messages
                role = "Usuario" if msg.get("role") == "user" else "Tú"
                content = msg.get("content", "")
                prompt += f"{role}: {content}\n"
            prompt += "\n"
        
        # Add product information
        if context.relevant_products and len(context.relevant_products) > 0:
            prompt += "PRODUCTOS RELEVANTES:\n"
            for product in context.relevant_products[:5]:
                prompt += f"- {product.get('name', 'N/A')}\n"
                prompt += f"  Precio: ${product.get('price', 'N/A')}\n"
                prompt += f"  Disponibilidad: {product.get('quantity', 0)} unidades\n"
                if product.get('description'):
                    desc = product['description'][:100]
                    prompt += f"  Descripción: {desc}...\n"
                prompt += "\n"
        
        # Add lead information
        if context.lead_info:
            lead_name = context.lead_info.get('name', '')
            if lead_name:
                prompt += f"INFORMACIÓN DEL CLIENTE:\n"
                prompt += f"Nombre: {lead_name}\n"
                prompt += f"Estado: {context.lead_info.get('status', 'nuevo')}\n\n"
        
        # Add action result if available
        if context.action_result:
            prompt += f"RESULTADO DE ACCIÓN EJECUTADA:\n"
            prompt += f"Acción: {context.action_type}\n"
            prompt += f"Resultado: {json.dumps(context.action_result, ensure_ascii=False)}\n\n"
        
        prompt += """Genera una respuesta natural y útil para el usuario basándote en toda la información proporcionada.
NO repitas lo que el usuario dijo, simplemente responde de manera natural y útil."""
        
        return prompt
    
    def _generate_fallback_response(self, context: PipelineContext) -> str:
        """
        Generate fallback template-based response when AI fails.
        """
        
        # Handle payment proof received specially
        if context.action_type == "payment_proof_received":
            return self._payment_proof_response(context)
        
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
            return "Interesante. Déjame ayudarte con eso. ¿Podrías darme más detalles?"
    
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
        
        # Check if QR payment was initiated
        if context.action_result:
            action = context.action_result.get("action", "")
            
            if action == "awaiting_payment_proof":
                # QR was sent, ask customer to scan
                return "✅ Te acabo de enviar el código QR. Por favor escanéalo con tu billetera digital para completar el pago."
            elif action == "sale_completed":
                # Non-QR payment, sale is complete
                return "¡Excelente! Tu compra ha sido confirmada. Nuestro equipo se pondrá en contacto contigo pronto."
            elif action == "qr_payment_failed":
                # QR failed, provide alternative
                return "Disculpa, tuvimos un problema al enviar el QR. Por favor intenta de nuevo o contacta con nuestro equipo."
            elif action == "qualify_lead":
                # Not ready yet
                return "¡Excelente! Me encantaría ayudarte con tu compra. ¿Cuál plan te interesa? (Plan Estándar $100/mes o Plan Premium $200/mes)"
        
        # Default response
        return "¡Excelente! Me encantaría ayudarte con tu compra. ¿Qué producto te gustaría adquirir?"
    
    def _payment_proof_response(self, context: PipelineContext) -> str:
        """Generate response when payment proof is received."""
        
        if context.action_result:
            if context.action_result.get("supervisor_notified"):
                return "✅ ¡Perfecto! Recibimos tu comprobante de pago. Nuestro equipo está revisando tu solicitud y se pondrá en contacto contigo pronto. ¡Gracias por tu compra! 🎉"
            else:
                return "⚠️ Recibimos tu comprobante, pero hubo un problema al notificar a nuestro equipo. Por favor contacta con nosotros directamente."
        
        return "✅ ¡Gracias! Recibimos tu comprobante. Estamos procesando tu solicitud."
    
    async def _save_messages_to_conversation(self, context: PipelineContext):
        """Save incoming and outgoing messages to conversation history."""
        
        from app.core.database import get_session_factory
        from app.models import SalesConversation
        from app.core.redis_client import cache_delete, build_conversation_cache_key
        from sqlalchemy import select
        from sqlalchemy.orm import attributes
        
        session_factory = get_session_factory()
        async with session_factory() as db:
            stmt = select(SalesConversation).where(
                SalesConversation.id == context.conversation_id
            )
            result = await db.execute(stmt)
            conversation = result.scalar_one_or_none()
            
            if conversation:
                # For batched messages, save all user messages individually
                if context.is_batch and context.batch_messages:
                    for msg in context.batch_messages:
                        conversation.add_message(
                            role="user",
                            content=msg.get("body", ""),
                            message_sid=msg.get("message_sid"),
                            intent=context.intent.value if context.intent else None,
                            intent_confidence=context.intent_confidence
                        )
                    
                    self.log_info(
                        "batch_messages_saved",
                        count=len(context.batch_messages),
                        conversation_id=context.conversation_id
                    )
                else:
                    # Single message - original behavior
                    conversation.add_message(
                        role="user",
                        content=context.message_body,
                        message_sid=context.message_sid,
                        intent=context.intent.value if context.intent else None,
                        intent_confidence=context.intent_confidence
                    )
                
                # Add single assistant response (whether batch or not)
                conversation.add_message(
                    role="assistant",
                    content=context.response_text,
                    message_sid=context.action_result.get("message_sid") if context.action_result else None,
                    action_type=context.action_type
                )
                
                # Update state machine fields
                if context.current_state is not None:
                    conversation.current_state = context.current_state
                conversation.cart_contents = context.cart_contents
                conversation.fulfillment_type = context.fulfillment_type
                
                # Mark the fields as modified so SQLAlchemy detects the change
                attributes.flag_modified(conversation, "messages")
                attributes.flag_modified(conversation, "cart_contents")
                
                await db.commit()
                
                # Invalidate cached conversation history
                cache_key = build_conversation_cache_key(context.tenant_id, context.conversation_id)
                await cache_delete(cache_key)
                
                self.log_info(
                    "messages_saved_to_conversation",
                    conversation_id=context.conversation_id,
                    total_messages=conversation.message_count,
                    cache_invalidated=True
                )
