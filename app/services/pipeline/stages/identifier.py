"""
Identification stage - identifies tenant, agent instance, and conversation.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.pipeline import PipelineContext
from app.services.pipeline.base import BasePipelineStage, IdentificationError
from app.models import AgentInstance, Tenant, SalesConversation
from app.core.database import get_session_factory
from app.core.redis_client import (
    cache_get,
    cache_set,
    build_agent_by_phone_cache_key,
    build_tenant_cache_key,
    build_agent_cache_key,
    build_conversation_cache_key,
)


class IdentificationStage(BasePipelineStage):
    """
    Stage 2: Identify tenant, agent instance, and conversation.
    
    Process:
    1. Lookup agent instance by recipient phone number (cached)
    2. Load tenant configuration (cached)
    3. Find or create conversation with this sender
    4. Extract agent configuration from JSON
    """
    
    async def process(self, context: PipelineContext) -> PipelineContext:
        """Identify tenant and agent for this message."""
        
        self.log_info(
            "identifying_agent",
            recipient_phone=context.recipient_phone
        )
        
        # Try to get agent from cache first
        cache_key = build_agent_by_phone_cache_key(context.recipient_phone)
        cached_agent = await cache_get(cache_key)
        
        if cached_agent:
            self.log_info("agent_found_in_cache", agent_id=cached_agent.get("id"))
            context.agent_instance_id = cached_agent.get("id")
            context.tenant_id = cached_agent.get("tenant_id")
            context.agent_config = cached_agent.get("configuration")
        else:
            # Query database
            session_factory = get_session_factory()
            async with session_factory() as db:
                agent = await self._find_agent_by_phone(db, context.recipient_phone)
                
                if not agent:
                    raise IdentificationError(
                        f"No agent instance found for phone {context.recipient_phone}",
                        stage=self.stage_name,
                        context=context
                    )
                
                context.agent_instance_id = agent.id
                context.tenant_id = agent.tenant_id
                context.agent_config = agent.configuration
                
                # Cache for future requests (phone-based lookup)
                await cache_set(cache_key, {
                    "id": agent.id,
                    "tenant_id": agent.tenant_id,
                    "configuration": agent.configuration,
                    "agent_type": agent.agent_type,
                })
                
                # Also cache tenant-scoped agent data for other lookups
                from app.core.redis_client import build_agent_cache_key
                tenant_agent_key = build_agent_cache_key(agent.tenant_id, agent.id)
                await cache_set(tenant_agent_key, {
                    "id": agent.id,
                    "tenant_id": agent.tenant_id,
                    "configuration": agent.configuration,
                    "agent_type": agent.agent_type,
                    "phone_number": agent.phone_number,
                })
                
                self.log_info(
                    "agent_identified",
                    agent_id=agent.id,
                    tenant_id=agent.tenant_id,
                    agent_type=agent.agent_type
                )
        
        # Find or create conversation
        session_factory = get_session_factory()
        async with session_factory() as db:
            conversation = await self._find_or_create_conversation(
                db,
                context.agent_instance_id,
                context.sender_phone
            )
            context.conversation_id = conversation.id
        
        self.log_info(
            "identification_completed",
            agent_id=context.agent_instance_id,
            tenant_id=context.tenant_id,
            conversation_id=context.conversation_id
        )
        
        return context
    
    async def _find_agent_by_phone(
        self,
        db: AsyncSession,
        phone_number: str
    ) -> AgentInstance | None:
        """Find agent instance by phone number."""
        
        # Remove 'whatsapp:' prefix if present
        clean_phone = phone_number.replace("whatsapp:", "")
        
        stmt = select(AgentInstance).where(
            AgentInstance.phone_number == clean_phone,
            AgentInstance.active == True
        )
        
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    
    async def _find_or_create_conversation(
        self,
        db: AsyncSession,
        agent_instance_id: int,
        sender_phone: str
    ) -> SalesConversation:
        """
        Find existing conversation or create new one.
        
        For now, we use a simple approach: one conversation per agent+sender pair.
        In future, you might want to create new conversations after timeout or explicit reset.
        """
        
        # Hash the sender phone to use as external_user_id
        # In production, you might want to link this to a Lead ID
        external_user_id = hash(sender_phone) % (10 ** 8)  # Simple hash for now
        
        stmt = select(SalesConversation).where(
            SalesConversation.agent_instance_id == agent_instance_id,
            SalesConversation.external_user_id == external_user_id,
            SalesConversation.active == True
        )
        
        result = await db.execute(stmt)
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            # Create new conversation
            conversation = SalesConversation(
                agent_instance_id=agent_instance_id,
                external_user_id=external_user_id,
                messages=[]
            )
            db.add(conversation)
            await db.commit()
            await db.refresh(conversation)
            
            self.log_info(
                "conversation_created",
                conversation_id=conversation.id,
                agent_instance_id=agent_instance_id
            )
        
        return conversation
