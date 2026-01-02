"""
Context builder stage - builds conversation context with history and data.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.pipeline import PipelineContext
from app.services.pipeline.base import BasePipelineStage
from app.models import SalesConversation, InventoryTenant, Lead, AgentInstance
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
            conversation_id=context.conversation_id
        )
        
        session_factory = get_session_factory()
        async with session_factory() as db:
            # Load agent configuration (with tenant-scoped caching)
            agent_cache_key = build_agent_cache_key(context.tenant_id, context.agent_instance_id)
            cached_agent_config = await cache_get(agent_cache_key)
            
            if cached_agent_config:
                context.agent_config = cached_agent_config
                self.log_info("agent_config_from_cache", agent_instance_id=context.agent_instance_id)
            else:
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
            
            # Load relevant inventory based on intent (with tenant-scoped caching)
            if context.intent in ["product_inquiry", "pricing_question", "availability_check"]:
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
            
            # Load lead info if exists (no caching - data changes frequently)
            # TODO: Refactorizar logica de lead (Ferru)
            context.lead_info = await self._load_lead_info(
                db,
                context.tenant_id,
                context.sender_phone
            )
        
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
        
        Returns a normalized configuration with defaults for missing values.
        """
        stmt = select(AgentInstance).where(
            AgentInstance.id == agent_instance_id
        )
        
        result = await db.execute(stmt)
        agent = result.scalar_one_or_none()
        
        if not agent or not agent.configuration:
            # Return minimal default config
            return self._get_default_agent_config()
        
        # Merge with defaults to ensure all required fields exist
        config = self._get_default_agent_config()
        self._deep_merge(config, agent.configuration)
        
        return config
    
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
            "sales_process": {
                "qualification_questions": [],
                "product_presentation_style": "benefits_focused",
                "pricing_strategy": "transparent",
                "upsell_enabled": True,
                "cross_sell_enabled": True,
                "discount_authority": False,
                "max_discount_percent": 0
            },
            "lead_management": {
                "auto_create_leads": True,
                "auto_follow_up": True,
                "follow_up_schedule": [1, 3, 7],  # Days after last interaction
                "follow_up_messages": [
                    {
                        "day": 1,
                        "message": "Hola {name}, ¿ya decidiste sobre los productos que te mostré ayer? ¿Tienes alguna pregunta?"
                    },
                    {
                        "day": 3,
                        "message": "Hola {name}, solo quería recordarte que tenemos esos productos disponibles. ¿Te interesa hacer el pedido?"
                    },
                    {
                        "day": 7,
                        "message": "Hola {name}, esta semana tenemos una promoción especial. ¿Te gustaría saber más?"
                    }
                ],
                "qualification_score_threshold": 75,
                "hot_lead_actions": [
                    "notify_sales_team",
                    "priority_response"
                ],
                "lead_scoring_rules": {
                    "product_inquiry": 10,
                    "pricing_question": 15,
                    "availability_check": 20,
                    "purchase_intent": 40,
                    "objection_handled": 5
                }
            },
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
        limit: int = 4
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
