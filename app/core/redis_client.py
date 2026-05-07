"""
Redis client for caching tenant configs, agent instances, and conversation state.
"""

from typing import Optional, Any, List
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


# Message batching operations

async def list_push(key: str, value: Any) -> int:
    """
    Push value to the left of a Redis list (LPUSH).
    
    Args:
        key: List key
        value: Value to push (will be JSON serialized)
    
    Returns:
        Length of the list after push
    """
    redis = get_redis_client()
    serialized = json.dumps(value)
    return await redis.lpush(key, serialized)


async def list_pop(key: str) -> Optional[Any]:
    """
    Pop value from the right of a Redis list (RPOP).
    
    Args:
        key: List key
    
    Returns:
        Deserialized value or None if list is empty
    """
    redis = get_redis_client()
    value = await redis.rpop(key)
    if value is None:
        return None
    return json.loads(value)


async def list_range(key: str, start: int = 0, end: int = -1) -> List[Any]:
    """
    Get range of values from a Redis list (LRANGE).
    
    Args:
        key: List key
        start: Start index (0-based)
        end: End index (-1 for all)
    
    Returns:
        List of deserialized values
    """
    redis = get_redis_client()
    values = await redis.lrange(key, start, end)
    return [json.loads(v) for v in values]


async def list_length(key: str) -> int:
    """
    Get length of a Redis list (LLEN).
    
    Args:
        key: List key
    
    Returns:
        Length of the list
    """
    redis = get_redis_client()
    return await redis.llen(key)


async def list_delete(key: str) -> bool:
    """
    Delete a Redis list.
    
    Args:
        key: List key
    
    Returns:
        True if key was deleted
    """
    redis = get_redis_client()
    result = await redis.delete(key)
    return result > 0


async def acquire_lock(key: str, ttl: int = 10) -> bool:
    """
    Acquire a distributed lock using Redis SETNX.
    
    Args:
        key: Lock key
        ttl: Time to live in seconds
    
    Returns:
        True if lock was acquired, False if already locked
    """
    redis = get_redis_client()
    result = await redis.set(key, "locked", nx=True, ex=ttl)
    return result is not None


async def release_lock(key: str) -> bool:
    """
    Release a distributed lock.
    
    Args:
        key: Lock key
    
    Returns:
        True if lock was released
    """
    redis = get_redis_client()
    result = await redis.delete(key)
    return result > 0


def build_batch_queue_key(agent_phone: str, user_phone: str) -> str:
    """Build cache key for message batch queue."""
    return f"batch_queue:{agent_phone}:{user_phone}"


def build_batch_lock_key(agent_phone: str, user_phone: str) -> str:
    """Build cache key for batch processing lock."""
    return f"batch_lock:{agent_phone}:{user_phone}"


def build_msg_dedup_key(message_sid: str) -> str:
    """
    Build a dedup key for a Twilio message SID.
    This key persists beyond the batch queue lifetime so Twilio retries
    (which arrive after the queue is deleted) are still rejected.
    """
    return f"dedup_msg:{message_sid}"


async def set_msg_dedup(message_sid: str, ttl: int = 300) -> None:
    """Mark a message SID as seen. TTL default is 5 minutes."""
    redis = get_redis_client()
    await redis.set(build_msg_dedup_key(message_sid), "1", ex=ttl)


async def is_msg_duplicate(message_sid: str) -> bool:
    """Return True if this message SID was already accepted for processing."""
    redis = get_redis_client()
    return await redis.exists(build_msg_dedup_key(message_sid)) > 0
