"""
Context builder stage - builds conversation context with history and data.
"""

from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.pipeline import PipelineContext
from app.services.pipeline.base import BasePipelineStage
from app.models import SalesConversation, InventoryTenant, Lead, AgentInstance, ConfigurationTenant
from app.core.database import get_session_factory
from app.core.redis_client import (
    cache_get,
    cache_set,
    build_conversation_cache_key,
    build_inventory_cache_key,
    build_agent_cache_key,
)


class ContextBuilderStage(BasePipelineStage):
    """
    Stage 4: Build conversation context.
    
    Gathers:
    - Agent configuration (personality, prompts, settings)
    - Recent conversation history
    - Relevant product/inventory data
    - Lead information (if exists)
    """
    
    async def process(self, context: PipelineContext) -> PipelineContext:
        """Build conversation context for AI response generation."""
        
        self.log_info(
            "building_context",
            conversation_id=context.conversation_id,
            is_batch=context.is_batch,
            batch_size=len(context.batch_messages) if context.batch_messages else 0
        )
        
        session_factory = get_session_factory()
        async with session_factory() as db:
            # Load agent configuration (with tenant-scoped caching)
            agent_cache_key = build_agent_cache_key(context.tenant_id, context.agent_instance_id)
            cached_agent_data = await cache_get(agent_cache_key)
            
            # TEMPORARY: Force reload from DB to debug
            force_reload = True  # Set to False after debugging
            
            if cached_agent_data and not force_reload:
                # Handle both formats: dict with 'configuration' key (from identifier) 
                # or direct configuration dict (from context_builder)
                if isinstance(cached_agent_data, dict) and "configuration" in cached_agent_data:
                    context.agent_config = cached_agent_data["configuration"]
                else:
                    context.agent_config = cached_agent_data
                
                # Log cached values to verify
                cached_company = context.agent_config.get("tenant_info", {}).get("company_name", "NOT_SET")
                self.log_info(
                    "agent_config_from_cache",
                    agent_instance_id=context.agent_instance_id,
                    cached_company_name=cached_company,
                    warning="Using cached data - may be outdated if config changed recently"
                )
            else:
                if force_reload and cached_agent_data:
                    self.log_info(
                        "force_reload_from_db",
                        agent_instance_id=context.agent_instance_id,
                        reason="Debugging configuration - bypassing cache"
                    )
                
                context.agent_config = await self._load_agent_configuration(
                    db,
                    context.agent_instance_id
                )
                # Cache agent config for 1 hour (config doesn't change frequently)
                await cache_set(agent_cache_key, context.agent_config, ttl=3600)
            
            # Load conversation history (with tenant-scoped caching)
            cache_key = build_conversation_cache_key(context.tenant_id, context.conversation_id)
            cached_history = await cache_get(cache_key)
            
            if cached_history:
                context.conversation_history = cached_history
                self.log_info("conversation_history_from_cache", conversation_id=context.conversation_id)
            else:
                context.conversation_history = await self._load_conversation_history(
                    db,
                    context.conversation_id
                )
                # Cache for 5 minutes (conversation data changes frequently)
                await cache_set(cache_key, context.conversation_history, ttl=300)
            
            # For batched messages, combine all message bodies for better context
            if context.is_batch and context.batch_messages:
                # Combine messages with newlines for AI context
                combined_messages = []
                for msg in context.batch_messages:
                    combined_messages.append(msg.get("body", ""))
                context.message_body = "\n".join(combined_messages)
                
                self.log_info(
                    "batched_messages_combined",
                    original_count=len(context.batch_messages),
                    combined_length=len(context.message_body)
                )
            
            # Load relevant inventory based on intent (with tenant-scoped caching)
            if context.intent in ["product_inquiry", "pricing_question", "availability_check", "purchase_intent"]:
                inventory_cache_key = build_inventory_cache_key(context.tenant_id)
                cached_inventory = await cache_get(inventory_cache_key)
                
                if cached_inventory:
                    context.relevant_products = cached_inventory[:5]  # Limit to 5
                    self.log_info("inventory_from_cache", tenant_id=context.tenant_id)
                else:
                    context.relevant_products = await self._load_relevant_products(
                        db,
                        context.tenant_id,
                        context.message_body
                    )
                    # Cache inventory for 10 minutes
                    await cache_set(inventory_cache_key, context.relevant_products, ttl=600)
            
            # Lead info already populated by IdentificationStage — skip re-query
            if not context.lead_info:
                context.lead_info = await self._load_lead_info(
                    db,
                    context.tenant_id,
                    context.sender_phone
                )
        
        # Add specific instructions based on ConversationState
        from app.schemas.pipeline import ConversationState
        state_instructions = ""
        current_state = context.current_state
        if current_state == ConversationState.BROWSING:
            state_instructions = "The user is currently browsing. Answer their questions about our products."
        elif current_state == ConversationState.CART_BUILDING:
            state_instructions = f"The user is adding items to their cart. Current cart: {context.cart_contents}. Ask if they need anything else before checkout."
        elif current_state == ConversationState.FULFILLMENT_COORD:
            missing = [
                f for f in (context.agent_config or {}).get("checkout_requirements", ["Nombre completo", "Dirección de entrega", "NIT"])
                if not context.checkout_data.get(f)
            ]
            collected_str = ", ".join(f"{k}: {v}" for k, v in context.checkout_data.items()) if context.checkout_data else "ninguno aún"
            missing_str = ", ".join(missing) if missing else "todos recopilados"
            state_instructions = (
                f"El usuario está listo para finalizar su compra. "
                f"Datos ya recopilados: {collected_str}. "
                f"Datos que aún faltan: {missing_str}. "
                f"Pide amablemente la información faltante. NO envíes el QR ni menciones el pago "
                f"hasta que el usuario haya proporcionado todos los datos requeridos."
            )
        elif current_state == ConversationState.AWAITING_RECEIPT:
            proof_submitted = context.cart_contents.get("payment_proof_submitted", False)
            if proof_submitted:
                state_instructions = "El usuario ya envió su comprobante de pago y está esperando la aprobación del supervisor. Informa amablemente que estamos revisando su pago y que recibirán una confirmación pronto. NO pidas el comprobante de nuevo."
            else:
                state_instructions = "Estamos esperando que el usuario envíe una foto o captura de su comprobante de pago QR. No respondas preguntas no relacionadas. Recuérdales amablemente que deben enviar la imagen del comprobante."
            
        if state_instructions:
            if not context.agent_config.get("operations_info"):
                context.agent_config["operations_info"] = {}
            existing_process = context.agent_config["operations_info"].get("sales_process", "")
            context.agent_config["operations_info"]["sales_process"] = f"{existing_process}\n\nINSTRUCCIONES DE ESTADO ACTUAL:\n{state_instructions}".strip()

        self.log_info(
            "context_built",
            conversation_id=context.conversation_id,
            has_agent_config=context.agent_config is not None,
            history_messages=len(context.conversation_history),
            products_found=len(context.relevant_products),
            has_lead=context.lead_info is not None
        )
        
        return context
    
    async def _load_agent_configuration(
        self,
        db: AsyncSession,
        agent_instance_id: int
    ) -> dict:
        """
        Load agent configuration from database.
        
        Loads both agent-specific configuration from agent_instance
        and business-level configuration from configuration_tenant,
        then merges them into a unified structure.
        """
        # Load agent instance
        stmt = select(AgentInstance).where(
            AgentInstance.id == agent_instance_id
        )
        
        result = await db.execute(stmt)
        agent = result.scalar_one_or_none()
        
        if not agent:
            return self._get_default_config()
        
        # Load tenant configuration
        self.log_info(
            "querying_tenant_config",
            tenant_id=agent.tenant_id,
            agent_id=agent_instance_id
        )
        
        tenant_config_stmt = select(ConfigurationTenant).where(
            ConfigurationTenant.tenant_id == agent.tenant_id,
            ConfigurationTenant.active == True
        )
        
        tenant_config_result = await db.execute(tenant_config_stmt)
        tenant_config = tenant_config_result.scalar_one_or_none()
        
        # Log tenant configuration status
        if tenant_config:
            self.log_info(
                "tenant_config_loaded",
                tenant_id=agent.tenant_id,
                has_business=tenant_config.business is not None,
                has_contact=tenant_config.contact is not None,
                has_products=tenant_config.products is not None,
                has_operations=tenant_config.operations is not None
            )
        else:
            self.log_info(
                "tenant_config_not_found",
                tenant_id=agent.tenant_id,
                message="No active tenant configuration found - using defaults"
            )
        
        # Build unified configuration
        config = self._merge_configurations(
            agent_config=agent.configuration if agent.configuration else {},
            tenant_config=tenant_config
        )
        
        return config
    
    def _merge_configurations(
        self,
        agent_config: dict,
        tenant_config: Optional[ConfigurationTenant]
    ) -> dict:
        """
        Merge agent-specific and tenant-level configurations.
        
        Creates a unified configuration structure that includes both
        agent personality/settings and business information.
        """
        # Start with default structure
        config = self._get_default_config()
        
        # Merge agent-specific configuration
        self._deep_merge(config, agent_config)
        
        # Merge tenant business configuration if available
        if tenant_config:
            # Map tenant business config to tenant_info structure
            if tenant_config.business:
                tenant_info = config.setdefault("tenant_info", {})
                
                # Directly set values from tenant config (override defaults)
                if "company_name" in tenant_config.business:
                    tenant_info["company_name"] = tenant_config.business["company_name"]
                if "industry" in tenant_config.business:
                    tenant_info["industry"] = tenant_config.business["industry"]
                if "description" in tenant_config.business:
                    tenant_info["description"] = tenant_config.business["description"]
                if "company_size" in tenant_config.business:
                    tenant_info["company_size"] = tenant_config.business["company_size"]
                if "location" in tenant_config.business:
                    tenant_info["location"] = tenant_config.business["location"]
                if "website" in tenant_config.business:
                    tenant_info["website"] = tenant_config.business["website"]
                if "year_founded" in tenant_config.business:
                    tenant_info["year_founded"] = tenant_config.business["year_founded"]
                
                self.log_info(
                    "merging_business_config",
                    company_name=tenant_info.get("company_name"),
                    industry=tenant_info.get("industry"),
                    description=tenant_info.get("description", "")[:50] if tenant_info.get("description") else None
                )
            
            # Map contact information
            if tenant_config.contact:
                contact_info = config.setdefault("tenant_info", {}).setdefault("contact_info", {})
                if "contact_name" in tenant_config.contact:
                    contact_info["name"] = tenant_config.contact["contact_name"]
                if "contact_role" in tenant_config.contact:
                    contact_info["role"] = tenant_config.contact["contact_role"]
                if "contact_email" in tenant_config.contact:
                    contact_info["email"] = tenant_config.contact["contact_email"]
                if "contact_phone" in tenant_config.contact:
                    contact_info["phone"] = tenant_config.contact["contact_phone"]
            
            # Map products configuration
            if tenant_config.products:
                config["product_info"] = {
                    "unique_selling_points": tenant_config.products.get("unique_selling_points"),
                    "target_audience": tenant_config.products.get("target_audience"),
                    "payment_methods": tenant_config.products.get("payment_methods")
                }
            
            # Map operations configuration
            if tenant_config.operations:
                ops = tenant_config.operations
                config["operations_info"] = {
                    "sales_process": ops.get("sales_process"),
                    "common_questions": ops.get("common_questions"),
                    "objections": ops.get("objections"),
                    "closing_techniques": ops.get("closing_techniques"),
                    "response_time": ops.get("response_time"),
                    "languages": ops.get("languages"),
                    "competitors": ops.get("competitors"),
                    "additional_context": ops.get("additional_context")
                }
                
                # Map business hours if present in operations
                if ops.get("business_hours"):
                    config["business_hours"] = ops["business_hours"]
        
        # Log final merged configuration
        final_company = config.get("tenant_info", {}).get("company_name")
        final_industry = config.get("tenant_info", {}).get("industry")
        self.log_info(
            "config_merge_complete",
            final_company_name=final_company,
            final_industry=final_industry,
            has_product_info=bool(config.get("product_info")),
            has_operations_info=bool(config.get("operations_info"))
        )
        
        return config
    
    def _get_default_config(self) -> dict:
        """
        Get default configuration structure (renamed from _get_default_agent_config).
        
        This ensures all expected fields exist even if not in DB.
        """
        return self._get_default_agent_config()
    
    def _get_default_agent_config(self) -> dict:
        """
        Get default agent configuration structure.
        
        This ensures all expected fields exist even if not in DB.
        """
        return {
            "agent_info": {
                "name": "Sales Agent",
                "version": "1.0.0",
                "type": "sales"
            },
            "integrations": {
                "whatsapp_number": None
            },
            "tenant_info": {
                "company_name": "Unknown Company",
                "industry": "General",
                "description": "Sales business",
                "contact_info": {
                    "phone": None,
                    "email": None,
                    "address": None,
                    "website": None
                }
            },
            "personality": {
                "tone": "friendly",
                "formality_level": "casual",
                "language": "es",
                "greeting_style": "warm",
                "emoji_usage": "moderate",
                "response_length": "concise",
                "custom_phrases": {},
                "brand_voice": ""
            },
            "sales_process": {},
            "lead_management": {},
            "product_catalog": {},
            "response_settings": {
                "max_response_length": 500,
                "include_product_images": True,
                "include_pricing": True,
                "show_availability": True,
                "response_delay_seconds": 0,
                "typing_indicator_duration": 2
            },
            "business_hours": {
                "enabled": False,
                "timezone": "America/La_Paz",
                "schedule": {
                    "monday": {"open": "09:00", "close": "18:00"},
                    "tuesday": {"open": "09:00", "close": "18:00"},
                    "wednesday": {"open": "09:00", "close": "18:00"},
                    "thursday": {"open": "09:00", "close": "18:00"},
                    "friday": {"open": "09:00", "close": "18:00"},
                    "saturday": {"open": "10:00", "close": "14:00"},
                    "sunday": {"open": None, "close": None}
                },
                "after_hours_message": "Gracias por contactarnos. Nuestro horario de atención es de lunes a viernes de 9:00 a 18:00. Te responderemos a la brevedad."
            },
            "conversation_settings": {
                "context_messages_limit": 10,
                "session_timeout_minutes": 30,
                "handoff_to_human_keywords": ["hablar con persona", "agente humano", "representante"],
                "auto_handoff_enabled": False
            }
        }
    
    def _deep_merge(self, base: dict, override: dict) -> None:
        """
        Deep merge override dict into base dict.
        
        Modifies base in-place.
        """
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
    
    async def _load_conversation_history(
        self,
        db: AsyncSession,
        conversation_id: int,
        limit: int = 10
    ) -> list[dict]:
        """Load recent conversation messages."""
        
        stmt = select(SalesConversation).where(
            SalesConversation.id == conversation_id
        )
        
        result = await db.execute(stmt)
        conversation = result.scalar_one_or_none()
        
        if not conversation or not conversation.messages:
            return []
        
        # Return last N messages
        return conversation.get_conversation_history(limit)
    
    async def _load_relevant_products(
        self,
        db: AsyncSession,
        tenant_id: int,
        message_body: str
    ) -> list[dict]:
        """
        Load products relevant to the user's query.
        
        In production, use semantic search or vector embeddings.
        For now, just return active products.
        """
        
        stmt = select(InventoryTenant).where(
            InventoryTenant.tenant_id == tenant_id,
            InventoryTenant.active == True,
            InventoryTenant.quantity > 0
        ).limit(5)
        
        result = await db.execute(stmt)
        products = result.scalars().all()
        
        return [
            {
                "id": p.id,
                "name": p.product_name,
                "price": str(p.price),
                "quantity": p.quantity,
                "description": p.description
            }
            for p in products
        ]
    
    async def _load_lead_info(
        self,
        db: AsyncSession,
        tenant_id: int,
        phone: str
    ) -> dict | None:
        """Load lead information if exists."""
        
        clean_phone = phone.replace("whatsapp:", "").replace("+", "")
        
        stmt = select(Lead).where(
            Lead.tenant_id == tenant_id,
            Lead.phone.contains(clean_phone),
            Lead.active == True
        )
        
        result = await db.execute(stmt)
        lead = result.scalar_one_or_none()
        
        if not lead:
            return None
        
        return {
            "id": lead.id,
            "name": lead.name,
            "email": lead.email,
            "phone": lead.phone,
            "status": lead.status,
            "score": lead.score
        }
