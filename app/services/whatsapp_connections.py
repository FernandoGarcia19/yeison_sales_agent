"""
Per-agent WhatsApp provider configuration lookup.

Evolution is the default provider. Twilio remains available only as a
deprecated fallback for rollback scenarios.

Evolution host credentials are shared through process settings; only the
instance name is stored per agent.
"""

from __future__ import annotations

from typing import Any, Optional

import structlog
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_session_factory
from app.core.redis_client import cache_get, cache_set
from app.models.agent_instance import AgentInstance
from app.models.whatsapp_connection import WhatsAppConnection

logger = structlog.get_logger()

_CACHE_TTL = 600


def _normalize_phone(phone: str | None) -> str:
    if not phone:
        return ""
    return phone.replace("whatsapp:", "").strip()


def _cache_key(phone: str) -> str:
    return f"whatsapp_connection:{phone}"


def _build_connection_payload(connection: WhatsAppConnection, agent: AgentInstance) -> dict[str, Any]:
    provider = (connection.provider or settings.whatsapp_provider_default or "evolution").lower()

    return {
        "provider": provider,
        "agent_id": agent.id,
        "tenant_id": agent.tenant_id,
        "agent_phone": agent.phone_number,
        "whatsapp_phone_number": connection.whatsapp_phone_number or agent.phone_number,
        "evolution_instance_name": connection.evolution_instance_name,
        "twilio_subaccount_sid": connection.twilio_subaccount_sid,
        "twilio_auth_token": settings.twilio_auth_token,
        "messaging_service_sid": connection.messaging_service_sid,
    }


async def get_whatsapp_connection_for_phone(agent_phone: str) -> Optional[dict[str, Any]]:
    """Resolve the active WhatsApp provider configuration for an agent phone."""
    clean_phone = _normalize_phone(agent_phone)
    if not clean_phone:
        return None

    cached = await cache_get(_cache_key(clean_phone))
    if cached:
        return cached

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
            logger.warning("whatsapp_connection_agent_not_found", phone=clean_phone)
            return None

        conn_result = await db.execute(
            select(WhatsAppConnection).where(
                WhatsAppConnection.agent_instance_id == agent.id,
                WhatsAppConnection.active == True,  # noqa: E712
            )
        )
        connection = conn_result.scalar_one_or_none()

        if not connection:
            logger.warning("whatsapp_connection_not_found", agent_id=agent.id, phone=clean_phone)
            return None

        payload = _build_connection_payload(connection, agent)

    await cache_set(_cache_key(clean_phone), payload, ttl=_CACHE_TTL)
    return payload


async def get_whatsapp_connection_for_instance(instance_name: str) -> Optional[dict[str, Any]]:
    """Resolve the active WhatsApp provider configuration by Evolution instance name."""
    clean_instance = (instance_name or "").strip()
    if not clean_instance:
        return None

    session_factory = get_session_factory()
    async with session_factory() as db:
        conn_result = await db.execute(
            select(WhatsAppConnection).where(
                WhatsAppConnection.evolution_instance_name == clean_instance,
                WhatsAppConnection.active == True,  # noqa: E712
            )
        )
        connection = conn_result.scalar_one_or_none()

        if not connection:
            logger.warning("whatsapp_connection_instance_not_found", instance_name=clean_instance)
            return None

        agent_result = await db.execute(
            select(AgentInstance).where(
                AgentInstance.id == connection.agent_instance_id,
                AgentInstance.active == True,  # noqa: E712
            )
        )
        agent = agent_result.scalar_one_or_none()

        if not agent:
            logger.warning("whatsapp_connection_agent_missing_for_instance", instance_name=clean_instance)
            return None

        return _build_connection_payload(connection, agent)