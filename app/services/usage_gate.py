"""
Plan limit and rate limit for WhatsApp turns (Y-17).

Before a customer turn uses the models, the pipeline asks:
- the rate limit (Redis): at most RATE_PER_CUSTOMER turns per customer every 10
  minutes and RATE_PER_TENANT per business per hour. Over it, the turn is dropped
  without an answer (spam or a loop between bots).
- the monthly plan limit, which the panel owns (GET /internal/usage/sales-gate,
  cached GATE_CACHE_SECONDS per business). "blocked" means the business passed
  its limit plus the grace: the customer gets a fixed message, once every
  HANDOFF_EVERY_SECONDS, and the supervisor is told.

Redis or the panel being down never stops a turn: the gate lets it through and logs it.
"""

import time
from typing import Optional

import httpx
import structlog

from app.core.config import settings
from app.core.redis_client import get_redis_client

logger = structlog.get_logger()

RATE_PER_CUSTOMER = (20, 600)
RATE_PER_TENANT = (300, 3600)
GATE_CACHE_SECONDS = 60
HANDOFF_EVERY_SECONDS = 12 * 3600

PLAN_LIMIT_MESSAGE = ("¡Gracias por escribirnos! En este momento te atenderá una persona de nuestro equipo; "
                      "te responderemos lo antes posible.")


async def _count(key: str, window: int) -> int:
    redis = get_redis_client()
    async with redis.pipeline(transaction=True) as pipe:
        count, _ = await pipe.incr(key).expire(key, window + 5).execute()
    return int(count)


async def allow_turn(tenant_id: int, agent_id: Optional[int], customer: str) -> bool:
    now = int(time.time())
    try:
        limit, window = RATE_PER_CUSTOMER
        if await _count(f"rate:customer:{agent_id}:{customer}:{now - now % window}", window) > limit:
            logger.warning("turn_rate_limited", scope="customer", tenant_id=tenant_id, customer=customer)
            return False
        limit, window = RATE_PER_TENANT
        if await _count(f"rate:tenant:{tenant_id}:{now - now % window}", window) > limit:
            logger.warning("turn_rate_limited", scope="tenant", tenant_id=tenant_id)
            return False
    except Exception as exc:
        logger.warning("rate_limit_skipped", error=str(exc))
    return True


async def plan_state(tenant_id: int) -> str:
    """'ok', 'warning', 'grace' or 'blocked' (the panel decides; 'ok' when it cannot be asked)."""
    key = f"usage:gate:{tenant_id}"
    try:
        cached = await get_redis_client().get(key)
        if cached:
            return str(cached)
    except Exception as exc:
        logger.warning("usage_gate_cache_unavailable", error=str(exc))
    if not settings.backend_api_url or not settings.backend_api_key:
        return "ok"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.backend_api_url.rstrip('/')}/internal/usage/sales-gate",
                                        params={"tenant_id": tenant_id},
                                        headers={"x-internal-key": settings.backend_api_key})
        response.raise_for_status()
        state = str(response.json().get("state") or "ok")
    except Exception as exc:
        logger.warning("usage_gate_unavailable", tenant_id=tenant_id, error=str(exc))
        return "ok"
    try:
        await get_redis_client().set(key, state, ex=GATE_CACHE_SECONDS)
    except Exception:
        pass
    return state


async def first_handoff(agent_id: Optional[int], customer: str) -> bool:
    """True the first time in HANDOFF_EVERY_SECONDS this customer hits the limit (answer and notify only then)."""
    try:
        return bool(await get_redis_client().set(f"usage:handoff:{agent_id}:{customer}", "1",
                                                 ex=HANDOFF_EVERY_SECONDS, nx=True))
    except Exception:
        return True
