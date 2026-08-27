"""
Schemas for inbound WhatsApp webhook requests and responses.

The request model is provider-neutral and keeps compatibility accessors for the
existing pipeline code during the cutover.
"""

from typing import Optional, Any
from pydantic import BaseModel, Field


class WhatsAppWebhookRequest(BaseModel):
    """Provider-neutral inbound WhatsApp webhook payload."""

    provider: str = Field(default="evolution", description="Webhook provider name")
    message_id: str = Field(..., description="Provider message identifier")
    sender_phone: str = Field(..., description="Sender phone number in E.164 format")
    recipient_phone: str = Field(..., description="Agent phone number in E.164 format")
    body: str = Field(default="", description="Message text content")
    profile_name: Optional[str] = Field(None, description="Sender display name")
    media_urls: list[str] = Field(default_factory=list, description="Media URLs")
    instance_name: Optional[str] = Field(None, description="Evolution instance name, when available")
    raw_payload: dict[str, Any] = Field(default_factory=dict, description="Original provider payload")

    @property
    def MessageSid(self) -> str:
        return self.message_id

    @property
    def SmsSid(self) -> str:
        return self.message_id

    @property
    def AccountSid(self) -> str:
        return self.instance_name or self.provider

    @property
    def MessagingServiceSid(self) -> Optional[str]:
        return self.instance_name

    @property
    def From(self) -> str:
        return self.sender_phone if self.sender_phone.startswith("whatsapp:") else f"whatsapp:{self.sender_phone}"

    @property
    def To(self) -> str:
        return self.recipient_phone if self.recipient_phone.startswith("whatsapp:") else f"whatsapp:{self.recipient_phone}"

    @property
    def Body(self) -> str:
        return self.body

    @property
    def NumMedia(self) -> str:
        return str(len(self.media_urls))

    @property
    def ProfileName(self) -> Optional[str]:
        return self.profile_name

    @property
    def WaId(self) -> Optional[str]:
        return self.sender_phone

    @property
    def Latitude(self) -> Optional[str]:
        return None

    @property
    def Longitude(self) -> Optional[str]:
        return None

    def get_media_urls(self) -> list[str]:
        return list(self.media_urls)

    @property
    def has_media(self) -> bool:
        return bool(self.media_urls)


class TwilioWebhookRequest(WhatsAppWebhookRequest):
    """Deprecated compatibility alias for legacy Twilio webhook handling."""


class TwilioWebhookResponse(BaseModel):
    """Response to inbound WhatsApp webhooks."""

    status: str = Field(default="received", description="Processing status")
    message_sid: str = Field(..., description="Message identifier that was processed")
