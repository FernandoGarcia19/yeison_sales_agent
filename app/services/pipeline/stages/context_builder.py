"""
Context builder stage - builds conversation context with history and data.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.pipeline import PipelineContext
from app.services.pipeline.base import BasePipelineStage
from app.models import SalesConversation, InventoryTenant, Lead
from app.core.database import get_session_factory
from app.core.redis_client import (
    cache_get,
    cache_set,
    build_conversation_cache_key,
    build_inventory_cache_key,
)


class ContextBuilderStage(BasePipelineStage):
    """
    Stage 4: Build conversation context.
    
    Gathers:
    - Recent conversation history
    - Relevant product/inventory data
    - Lead information (if exists)
    - Agent configuration and prompts
    """
    
    async def process(self, context: PipelineContext) -> PipelineContext:
        """Build conversation context for AI response generation."""
        
        self.log_info(
            "building_context",
            conversation_id=context.conversation_id
        )
        
        session_factory = get_session_factory()
        async with session_factory() as db:
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
            context.lead_info = await self._load_lead_info(
                db,
                context.tenant_id,
                context.sender_phone
            )
        
        self.log_info(
            "context_built",
            conversation_id=context.conversation_id,
            history_messages=len(context.conversation_history),
            products_found=len(context.relevant_products),
            has_lead=context.lead_info is not None
        )
        
        return context
    
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
