"""
Redis client for caching tenant configs, agent instances, and conversation state.
"""

from typing import Optional, Any
import json
from redis.asyncio import Redis, ConnectionPool

from app.core.config import settings


# Global Redis client
_redis_client: Optional[Redis] = None
_connection_pool: Optional[ConnectionPool] = None


def get_redis_client() -> Redis:
    """
    Get or create the Redis client.
    
    Uses connection pooling for efficiency.
    """
    global _redis_client, _connection_pool
    
    if _redis_client is None:
        if not settings.redis_url:
            raise ValueError("REDIS_URL environment variable is not set")
        
        # Create connection pool
        _connection_pool = ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=10,
        )
        
        _redis_client = Redis(connection_pool=_connection_pool)
    
    return _redis_client


async def cache_get(key: str) -> Optional[Any]:
    """
    Get a value from cache.
    
    Automatically deserializes JSON data.
    """
    redis = get_redis_client()
    value = await redis.get(key)
    
    if value is None:
        return None
    
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


async def cache_set(
    key: str,
    value: Any,
    ttl: Optional[int] = None
) -> bool:
    """
    Set a value in cache.
    
    Automatically serializes objects to JSON.
    
    Args:
        key: Cache key
        value: Value to cache
        ttl: Time to live in seconds (default: from settings)
    
    Returns:
        True if successful
    """
    redis = get_redis_client()
    
    # Serialize value
    if isinstance(value, (dict, list)):
        serialized = json.dumps(value)
    else:
        serialized = str(value)
    
    # Use default TTL if not specified
    if ttl is None:
        ttl = settings.redis_ttl
    
    await redis.setex(key, ttl, serialized)
    return True


async def cache_delete(key: str) -> bool:
    """Delete a key from cache."""
    redis = get_redis_client()
    result = await redis.delete(key)
    return result > 0


async def cache_exists(key: str) -> bool:
    """Check if a key exists in cache."""
    redis = get_redis_client()
    return await redis.exists(key) > 0


async def close_redis_connection():
    """
    Close Redis connection.
    
    Should be called on application shutdown.
    """
    global _redis_client, _connection_pool
    
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
    
    if _connection_pool is not None:
        await _connection_pool.disconnect()
        _connection_pool = None


# Cache key builders for consistency
def build_tenant_cache_key(tenant_id: int) -> str:
    """Build cache key for tenant data."""
    return f"tenant:{tenant_id}"


def build_agent_cache_key(tenant_id: int, agent_id: int) -> str:
    """Build cache key for agent instance data (tenant-scoped)."""
    return f"tenant:{tenant_id}:agent:{agent_id}"


def build_agent_by_phone_cache_key(phone_number: str) -> str:
    """Build cache key for agent lookup by phone.
    
    Note: Phone lookup is not tenant-scoped because we don't know
    the tenant_id until AFTER we look up the agent by phone.
    Phone numbers should be globally unique per agent.
    """
    return f"agent:phone:{phone_number}"


def build_conversation_cache_key(tenant_id: int, conversation_id: int) -> str:
    """Build cache key for conversation context (tenant-scoped)."""
    return f"tenant:{tenant_id}:conversation:{conversation_id}"


def build_inventory_cache_key(tenant_id: int) -> str:
    """Build cache key for tenant inventory (tenant-scoped)."""
    return f"tenant:{tenant_id}:inventory"
