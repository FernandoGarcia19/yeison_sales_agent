"""
Per-tenant Twilio subaccount credential lookup.

Resolves agent phone number → (subaccount_sid, auth_token) by joining
AgentInstance and WhatsAppConnection, decrypting the stored token, and
caching the result in Redis so the DB is not hit on every webhook.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select

from app.core.database import get_session_factory
from app.core.encryption import EncryptionError, TokenCipher
from app.core.redis_client import cache_get, cache_set
from app.models.agent_instance import AgentInstance
from app.models.whatsapp_connection import WhatsAppConnection

logger = structlog.get_logger()

_CACHE_TTL = 600  # 10 minutes


def _cache_key(phone: str) -> str:
    return f"twilio_creds:{phone}"


async def get_subaccount_credentials(agent_phone: str) -> tuple[str | None, str | None]:
    """
    Return ``(subaccount_sid, auth_token)`` for the agent identified by
    *agent_phone* (E.164, with or without the ``whatsapp:`` prefix).

    Returns ``(None, None)`` when:
    - no AgentInstance matches the phone number, or
    - the agent has no WhatsAppConnection yet, or
    - the stored token cannot be decrypted (misconfigured ENCRYPTION_KEY).

    The result is cached in Redis for ``_CACHE_TTL`` seconds to keep the
    hot path (every inbound webhook) free of DB round-trips.
    """
    clean_phone = agent_phone.replace("whatsapp:", "")

    cached = await cache_get(_cache_key(clean_phone))
    if cached:
        return cached.get("sid"), cached.get("token")

    session_factory = get_session_factory()
    async with session_factory() as db:
        agent_result = await db.execute(
            select(AgentInstance).where(
                AgentInstance.phone_number == clean_phone,
                AgentInstance.active == True,  # noqa: E712
            )
        )
        agent = agent_result.scalar_one_or_none()

        if not agent:
            logger.warning("twilio_creds_agent_not_found", phone=clean_phone)
            return None, None

        conn_result = await db.execute(
            select(WhatsAppConnection).where(
                WhatsAppConnection.agent_instance_id == agent.id,
                WhatsAppConnection.active == True,  # noqa: E712
            )
        )
        connection = conn_result.scalar_one_or_none()

        if not connection or not connection.twilio_auth_token_encrypted:
            logger.warning(
                "twilio_creds_no_connection",
                agent_id=agent.id,
                phone=clean_phone,
            )
            return None, None

        try:
            auth_token = TokenCipher.decrypt_token(connection.twilio_auth_token_encrypted)
        except EncryptionError as exc:
            logger.error(
                "twilio_creds_decrypt_failed",
                agent_id=agent.id,
                error=str(exc),
            )
            return None, None

    await cache_set(
        _cache_key(clean_phone),
        {"sid": connection.twilio_subaccount_sid, "token": auth_token},
        ttl=_CACHE_TTL,
    )
    return connection.twilio_subaccount_sid, auth_token
