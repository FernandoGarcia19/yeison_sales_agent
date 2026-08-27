"""
Downloads media files from WhatsApp provider-authenticated URLs.

Twilio support remains deprecated; Evolution is the primary path.
"""

import httpx
import structlog

from app.core.config import settings
from app.services.whatsapp_connections import get_whatsapp_connection_for_phone

logger = structlog.get_logger()


async def download_whatsapp_media(
    url: str,
    provider: str = "evolution",
    agent_phone: str | None = None,
) -> tuple[bytes, str]:
    """
    Download a media file from a WhatsApp provider URL.
    Returns (file_bytes, content_type).
    content_type is normalized (no charset suffix), e.g. 'image/jpeg' or 'application/pdf'.
    """
    provider = (provider or "evolution").lower()
    headers = {}
    auth = None

    if provider == "twilio":
        auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    else:
        connection = await get_whatsapp_connection_for_phone(agent_phone) if agent_phone else None
        api_key = (connection or {}).get("evolution_api_key") or settings.evolution_api_key
        if api_key:
            headers["apikey"] = api_key

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            url,
            auth=auth,
            headers=headers,
            follow_redirects=True,
        )
        response.raise_for_status()

    raw_ct = response.headers.get("content-type", "image/jpeg")
    content_type = raw_ct.split(";")[0].strip()

    logger.info(
        "whatsapp_media_downloaded",
        provider=provider,
        url=url,
        content_type=content_type,
        size_bytes=len(response.content),
    )

    return response.content, content_type


async def download_twilio_media(url: str) -> tuple[bytes, str]:
    """Deprecated compatibility wrapper for legacy Twilio media downloads."""
    return await download_whatsapp_media(url, provider="twilio")
