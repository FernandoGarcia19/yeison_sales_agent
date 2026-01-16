"""
Tenant service - utilities for tenant identification and data isolation.
"""

from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.models import AgentInstance, Tenant
from app.core.redis_client import cache_get, cache_set

logger = structlog.get_logger()


class TenantService:
    """
    Service for tenant-related operations in a multitenant system.
    
    Ensures proper data isolation and tenant identification.
    """
    
    @staticmethod
    async def identify_tenant_by_phone(
        db: AsyncSession,
        phone_number: str,
        use_cache: bool = True
    ) -> Optional[tuple[int, int, dict]]:
        """
        Identify tenant by WhatsApp phone number.
        
        Args:
            db: Database session
            phone_number: WhatsApp phone number (E.164 format)
            use_cache: Whether to use Redis cache
        
        Returns:
            Tuple of (tenant_id, agent_instance_id, agent_config) or None if not found
        """
        
        # Clean phone number
        clean_phone = phone_number.replace("whatsapp:", "").strip()
        
        # Try cache first
        if use_cache:
            cache_key = f"tenant:phone:{clean_phone}"
            cached = await cache_get(cache_key)
            if cached:
                logger.info(
                    "tenant_identified_from_cache",
                    phone=clean_phone,
                    tenant_id=cached.get("tenant_id")
                )
                return (
                    cached["tenant_id"],
                    cached["agent_instance_id"],
                    cached["configuration"]
                )
        
        # Query database
        stmt = select(AgentInstance).where(
            AgentInstance.phone_number == clean_phone,
            AgentInstance.active == True
        )
        
        result = await db.execute(stmt)
        agent = result.scalar_one_or_none()
        
        if not agent:
            logger.warning(
                "no_agent_found_for_phone",
                phone=clean_phone
            )
            return None
        
        # Cache for 1 hour
        if use_cache:
            cache_key = f"tenant:phone:{clean_phone}"
            await cache_set(
                cache_key,
                {
                    "tenant_id": agent.tenant_id,
                    "agent_instance_id": agent.id,
                    "configuration": agent.configuration,
                    "agent_type": agent.agent_type
                },
                ttl=3600
            )
        
        logger.info(
            "tenant_identified",
            phone=clean_phone,
            tenant_id=agent.tenant_id,
            agent_id=agent.id
        )
        
        return (agent.tenant_id, agent.id, agent.configuration)
    
    @staticmethod
    async def get_tenant_info(
        db: AsyncSession,
        tenant_id: int,
        use_cache: bool = True
    ) -> Optional[dict]:
        """
        Get tenant information.
        
        Args:
            db: Database session
            tenant_id: Tenant ID
            use_cache: Whether to use Redis cache
        
        Returns:
            Tenant information dict or None
        """
        
        # Try cache first
        if use_cache:
            cache_key = f"tenant:info:{tenant_id}"
            cached = await cache_get(cache_key)
            if cached:
                return cached
        
        # Query database
        stmt = select(Tenant).where(
            Tenant.id == tenant_id,
            Tenant.active == True
        )
        
        result = await db.execute(stmt)
        tenant = result.scalar_one_or_none()
        
        if not tenant:
            return None
        
        tenant_info = {
            "id": tenant.id,
            "name": tenant.name,
            "email": tenant.email,
            "profile_image": getattr(tenant, "profile_image", None),
            "active": tenant.active
        }
        
        # Cache for 1 hour
        if use_cache:
            cache_key = f"tenant:info:{tenant_id}"
            await cache_set(cache_key, tenant_info, ttl=3600)
        
        return tenant_info
    
    @staticmethod
    async def validate_tenant_access(
        db: AsyncSession,
        tenant_id: int,
        resource_id: int,
        resource_type: str
    ) -> bool:
        """
        Validate that a resource belongs to a tenant.
        
        Security check to prevent cross-tenant data access.
        
        Args:
            db: Database session
            tenant_id: Tenant ID trying to access
            resource_id: Resource ID being accessed
            resource_type: Type of resource ('conversation', 'lead', 'inventory', etc.)
        
        Returns:
            True if tenant has access, False otherwise
        """
        
        # Import models dynamically to avoid circular imports
        from app.models import SalesConversation, Lead, InventoryTenant
        
        if resource_type == "conversation":
            stmt = select(SalesConversation).where(
                SalesConversation.id == resource_id
            ).join(AgentInstance).where(
                AgentInstance.tenant_id == tenant_id
            )
            result = await db.execute(stmt)
            return result.scalar_one_or_none() is not None
        
        elif resource_type == "lead":
            stmt = select(Lead).where(
                Lead.id == resource_id,
                Lead.tenant_id == tenant_id
            )
            result = await db.execute(stmt)
            return result.scalar_one_or_none() is not None
        
        elif resource_type == "inventory":
            stmt = select(InventoryTenant).where(
                InventoryTenant.id == resource_id,
                InventoryTenant.tenant_id == tenant_id
            )
            result = await db.execute(stmt)
            return result.scalar_one_or_none() is not None
        
        elif resource_type == "agent_instance":
            stmt = select(AgentInstance).where(
                AgentInstance.id == resource_id,
                AgentInstance.tenant_id == tenant_id
            )
            result = await db.execute(stmt)
            return result.scalar_one_or_none() is not None
        
        else:
            logger.warning(
                "unknown_resource_type_for_validation",
                resource_type=resource_type
            )
            return False
    
    @staticmethod
    def build_tenant_cache_key(tenant_id: int, key_suffix: str) -> str:
        """
        Build a tenant-scoped cache key.
        
        Example:
            build_tenant_cache_key(123, "inventory") -> "tenant:123:inventory"
        """
        return f"tenant:{tenant_id}:{key_suffix}"
