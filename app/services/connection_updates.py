"""
Evolution's connection.update, forwarded to the panel (Y-63).

Every instance is created subscribed to CONNECTION_UPDATE, but nothing read it: the panel kept showing
"connected" for a WhatsApp that had dropped, and the business found out when its customers complained.
The panel owns whatsapp_connection (this service only reads it), so the event goes there: it stores the
state and tells the owners on Telegram when a working WhatsApp disconnects.
"""

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()


async def forward(instance: str, state: str) -> bool:
    """POST the new state to the panel. Never raises: a lost update only delays the notice."""
    if not settings.backend_api_url or not settings.backend_api_key:
        logger.warning("connection_update_not_forwarded", reason="panel_not_configured", instance=instance)
        return False
    url = f"{settings.backend_api_url.rstrip('/')}/internal/whatsapp/connection-update"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json={"instance": instance, "state": state},
                                         headers={"x-internal-key": settings.backend_api_key})
        if response.status_code >= 400:
            logger.warning("connection_update_rejected", instance=instance, state=state, status=response.status_code)
            return False
        logger.info("connection_update_forwarded", instance=instance, state=state)
        return True
    except Exception as exc:
        logger.warning("connection_update_not_forwarded", reason=str(exc), instance=instance)
        return False
