"""
Deprecated Twilio credential lookup.

Twilio remains available only for rollback and legacy tenants. New code should
use app.services.whatsapp_connections to resolve per-agent provider settings.
"""

from __future__ import annotations

import structlog

from app.services.whatsapp_connections import get_whatsapp_connection_for_phone

logger = structlog.get_logger()

async def get_subaccount_credentials(agent_phone: str) -> tuple[str | None, str | None]:
    """Return legacy Twilio subaccount credentials when the agent still uses Twilio."""
    connection = await get_whatsapp_connection_for_phone(agent_phone)

    if not connection or connection.get("provider") != "twilio":
        logger.warning("twilio_credentials_deprecated_or_missing", phone=agent_phone)
        return None, None

    return connection.get("twilio_subaccount_sid"), connection.get("twilio_auth_token")
