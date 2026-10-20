"""
WhatsApp message sending.

Evolution API is now the primary provider. Twilio remains available only as a
deprecated fallback for legacy tenants and rollback scenarios.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

import structlog
from twilio.rest import Client

from app.core.config import settings
from app.integrations.whatsapp.evolution import send_evolution_message
from app.services.whatsapp_connections import get_whatsapp_connection_for_phone
from app.services.twilio_credentials import get_subaccount_credentials

logger = structlog.get_logger()


@lru_cache(maxsize=64)
def _get_client(account_sid: str, auth_token: str) -> Client:
    """Return a cached Twilio Client for the given credentials."""
    return Client(account_sid, auth_token)


async def _resolve_client(agent_phone: str) -> Client:
    """
    Return a Twilio Client scoped to the subaccount that owns *agent_phone*.
    Falls back to the globally configured credentials when no subaccount is
    found (sandbox / unconfigured agent).
    """
    subaccount_sid, auth_token = await get_subaccount_credentials(agent_phone)

    if subaccount_sid and auth_token:
        return _get_client(subaccount_sid, auth_token)

    # Fallback — works for sandbox numbers and local dev.
    sid = settings.twilio_account_sid
    token = settings.twilio_auth_token
    if not sid or not token:
        raise ValueError(
            "No Twilio credentials available: subaccount lookup returned nothing "
            "and TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are not set."
        )
    return _get_client(sid, token)


async def _send_via_twilio(
    to: str,
    body: str,
    from_number: str,
    media_url: Optional[str] = None,
) -> str:
    clean_from = from_number.replace("whatsapp:", "")
    client = await _resolve_client(clean_from)

    if not to.startswith("whatsapp:"):
        to = f"whatsapp:{to}"
    if not from_number.startswith("whatsapp:"):
        from_number = f"whatsapp:{from_number}"

    message = client.messages.create(
        from_=from_number,
        to=to,
        body=body,
        media_url=[media_url] if media_url else None,
    )

    logger.warning(
        "legacy_twilio_message_sent",
        message_sid=message.sid,
        to=to,
        from_=from_number,
        status=message.status,
    )
    return message.sid


async def send_whatsapp_message(
    to: str,
    body: str,
    from_number: str,
    media_url: Optional[str] = None,
) -> str:
    """
    Send a WhatsApp message via the provider configured for *from_number*.

    Args:
        to: Recipient phone number (E.164 or whatsapp:+… format).
        body: Message text.
        from_number: Agent's WhatsApp number (used to look up the subaccount).
        media_url: Optional public media URL to attach.

    Returns:
        Provider message identifier.
    """
    try:
        connection = await get_whatsapp_connection_for_phone(from_number)
        provider = (connection or {}).get("provider") or settings.whatsapp_provider_default or "evolution"
        provider = provider.lower()

        if provider == "twilio":
            return await _send_via_twilio(to, body, from_number, media_url=media_url)

        return await send_evolution_message(to=to, body=body, from_number=from_number, media_url=media_url)

    except Exception as exc:
        logger.error(
            "failed_to_send_whatsapp_message",
            to=to,
            from_=from_number,
            provider=(connection or {}).get("provider") if 'connection' in locals() else None,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
