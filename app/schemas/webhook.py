"""
Schemas for Twilio webhook requests and responses.
"""

from typing import Optional
from pydantic import BaseModel, Field


class TwilioWebhookRequest(BaseModel):
    """
    Twilio WhatsApp webhook request payload.
    
    See: https://www.twilio.com/docs/whatsapp/api#message-status-webhooks
    """
    
    # Message identifiers
    MessageSid: str = Field(..., description="Unique message identifier")
    SmsSid: str = Field(..., description="SMS SID (same as MessageSid for WhatsApp)")
    
    # Account info
    AccountSid: str = Field(..., description="Twilio account SID")
    MessagingServiceSid: Optional[str] = Field(None, description="Messaging service SID")
    
    # From/To
    From: str = Field(..., description="Sender's WhatsApp number (format: whatsapp:+1234567890)")
    To: str = Field(..., description="Recipient's WhatsApp number (your agent)")
    
    # Message content
    Body: str = Field(default="", description="Message text content")
    NumMedia: str = Field(default="0", description="Number of media items")
    
    # Optional profile info
    ProfileName: Optional[str] = Field(None, description="WhatsApp profile name")
    WaId: Optional[str] = Field(None, description="WhatsApp ID")
    
    # Location (if shared)
    Latitude: Optional[str] = Field(None, description="Latitude if location shared")
    Longitude: Optional[str] = Field(None, description="Longitude if location shared")
    
    # Media URLs (supports up to 5 media items)
    MediaUrl0: Optional[str] = Field(None, description="First media URL")
    MediaContentType0: Optional[str] = Field(None, description="First media content type")
    MediaUrl1: Optional[str] = Field(None, description="Second media URL")
    MediaContentType1: Optional[str] = Field(None, description="Second media content type")
    MediaUrl2: Optional[str] = Field(None, description="Third media URL")
    MediaContentType2: Optional[str] = Field(None, description="Third media content type")
    MediaUrl3: Optional[str] = Field(None, description="Fourth media URL")
    MediaContentType3: Optional[str] = Field(None, description="Fourth media content type")
    MediaUrl4: Optional[str] = Field(None, description="Fifth media URL")
    MediaContentType4: Optional[str] = Field(None, description="Fifth media content type")
    
    @property
    def sender_phone(self) -> str:
        """Extract phone number from 'From' field (removes 'whatsapp:' prefix)."""
        return self.From.replace("whatsapp:", "")
    
    @property
    def recipient_phone(self) -> str:
        """Extract phone number from 'To' field (removes 'whatsapp:' prefix)."""
        return self.To.replace("whatsapp:", "")
    
    @property
    def has_media(self) -> bool:
        """Check if message contains media."""
        return int(self.NumMedia) > 0
    
    def get_media_urls(self) -> list[str]:
        """Get all media URLs from the message."""
        urls = []
        num_media = int(self.NumMedia)
        
        for i in range(num_media):
            media_url = getattr(self, f"MediaUrl{i}", None)
            if media_url:
                urls.append(media_url)
        
        return urls


class TwilioWebhookResponse(BaseModel):
    """
    Response to Twilio webhook.
    
    Twilio expects an empty 200 OK response or TwiML for automated responses.
    """
    
    status: str = Field(default="received", description="Processing status")
    message_sid: str = Field(..., description="Message SID that was processed")
