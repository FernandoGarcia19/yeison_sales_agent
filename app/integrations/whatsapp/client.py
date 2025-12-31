"""
WhatsApp client for sending messages via Twilio.
"""

from twilio.rest import Client
from typing import Optional
import structlog

from app.core.config import settings

logger = structlog.get_logger()

# Global Twilio client
_twilio_client: Optional[Client] = None


def get_twilio_client() -> Client:
    """Get or create Twilio client."""
    global _twilio_client
    
    if _twilio_client is None:
        if not settings.twilio_account_sid or not settings.twilio_auth_token:
            raise ValueError("Twilio credentials not configured")
        
        _twilio_client = Client(
            settings.twilio_account_sid,
            settings.twilio_auth_token
        )
    
    return _twilio_client


async def send_whatsapp_message(
    to: str,
    body: str,
    media_url: Optional[str] = None
) -> str:
    """
    Send WhatsApp message via Twilio.
    
    Args:
        to: Recipient phone number (E.164 format)
        body: Message content
        media_url: Optional media URL
    
    Returns:
        Message SID
    """
    
    client = get_twilio_client()
    
    # Ensure phone numbers have whatsapp: prefix
    if not to.startswith("whatsapp:"):
        to = f"whatsapp:{to}"
    
    from_number = settings.twilio_phone_number
    if not from_number.startswith("whatsapp:"):
        from_number = f"whatsapp:{from_number}"
    
    try:
        # Send message
        message = client.messages.create(
            from_=from_number,
            to=to,
            body=body,
            media_url=[media_url] if media_url else None
        )
        
        logger.info(
            "whatsapp_message_sent",
            message_sid=message.sid,
            to=to,
            status=message.status
        )
        
        return message.sid
    
    except Exception as e:
        logger.error(
            "failed_to_send_whatsapp_message",
            to=to,
            error=str(e),
            error_type=type(e).__name__
        )
        raise
