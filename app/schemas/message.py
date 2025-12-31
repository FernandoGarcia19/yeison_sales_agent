"""
Schemas for message requests and responses.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


class MessageRequest(BaseModel):
    """Request to send a message via WhatsApp."""
    
    to: str = Field(..., description="Recipient phone number (E.164 format)")
    body: str = Field(..., description="Message content")
    media_url: Optional[str] = Field(None, description="Optional media URL")


class MessageResponse(BaseModel):
    """Response after sending a message."""
    
    message_sid: str = Field(..., description="Twilio message SID")
    status: str = Field(..., description="Message status")
    to: str = Field(..., description="Recipient phone number")
    from_: str = Field(..., alias="from", description="Sender phone number")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Timestamp")


class MessageMetadata(BaseModel):
    """Metadata for a message in the conversation."""
    
    message_sid: Optional[str] = Field(None, description="Twilio message SID")
    intent: Optional[str] = Field(None, description="Classified intent")
    confidence: Optional[float] = Field(None, description="Intent confidence score")
    action_executed: Optional[str] = Field(None, description="Action that was executed")
    action_result: Optional[Dict[str, Any]] = Field(None, description="Result of action execution")
    processing_time_ms: Optional[float] = Field(None, description="Processing time in milliseconds")
