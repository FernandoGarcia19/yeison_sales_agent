"""
Downloads media files from Twilio-authenticated URLs.
Twilio media URLs require Basic Auth with account SID and auth token.
"""

import httpx
import structlog
from app.core.config import settings

logger = structlog.get_logger()


async def download_twilio_media(url: str) -> tuple[bytes, str]:
    """
    Download a media file from a Twilio URL.
    Returns (file_bytes, content_type).
    content_type is normalized (no charset suffix), e.g. 'image/jpeg' or 'application/pdf'.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            url,
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            follow_redirects=True,
        )
        response.raise_for_status()

    raw_ct = response.headers.get("content-type", "image/jpeg")
    content_type = raw_ct.split(";")[0].strip()

    logger.info(
        "twilio_media_downloaded",
        url=url,
        content_type=content_type,
        size_bytes=len(response.content),
    )

    return response.content, content_type
