"""
Schemas for pipeline processing context and results.
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum


class IntentType(str, Enum):
    """Possible intent types for customer messages."""
    
    GREETING = "greeting"
    PRODUCT_INQUIRY = "product_inquiry"
    PRICING_QUESTION = "pricing_question"
    AVAILABILITY_CHECK = "availability_check"
    PURCHASE_INTENT = "purchase_intent"
    OBJECTION = "objection"
    COMPLAINT = "complaint"
    HANDOFF_REQUEST = "handoff_request"
    CLOSING = "closing"
    GENERAL_QUESTION = "general_question"
    UNKNOWN = "unknown"


class PipelineStage(str, Enum):
    """Pipeline processing stages."""
    
    VALIDATION = "validation"
    IDENTIFICATION = "identification"
    CLASSIFICATION = "classification"
    CONTEXT_BUILDING = "context_building"
    ACTION_EXECUTION = "action_execution"
    RESPONSE_GENERATION = "response_generation"


class ConversationState(str, Enum):
    BROWSING = "browsing"
    CART_BUILDING = "cart_building"
    FULFILLMENT_COORD = "fulfillment_coord"
    AWAITING_RECEIPT = "awaiting_receipt"
    ORDER_COMPLETED = "order_completed"
    PAUSED = "paused"


class PipelineContext(BaseModel):
    """
    Context data that flows through the pipeline stages.
    
    Each stage can read from and write to this context.
    """
    
    # Input data
    message_sid: str = Field(..., description="Twilio message SID")
    sender_phone: str = Field(..., description="Sender's phone number")
    recipient_phone: str = Field(..., description="Agent's phone number")
    message_body: str = Field(..., description="Message text content")
    profile_name: Optional[str] = Field(None, description="WhatsApp profile name")
    media_urls: List[str] = Field(default_factory=list, description="Media URLs if any")
    
    # Batch processing fields
    is_batch: bool = Field(default=False, description="Whether this is a batched message set")
    batch_messages: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="List of batched messages if is_batch=True"
    )
    
    # Identification stage results
    tenant_id: Optional[int] = Field(None, description="Identified tenant ID")
    agent_instance_id: Optional[int] = Field(None, description="Identified agent instance ID")
    agent_config: Optional[Dict[str, Any]] = Field(None, description="Agent configuration")
    conversation_id: Optional[int] = Field(None, description="Conversation ID")
    lead_id: Optional[int] = Field(None, description="Lead ID if exists")
    
    # State Machine tracking
    current_state: Optional[ConversationState] = Field(None, description="Current conversation state")
    cart_contents: Dict[str, Any] = Field(default_factory=dict, description="Current cart items")
    checkout_data: Dict[str, Any] = Field(default_factory=dict, description="Current checkout structured data")
    fulfillment_type: Optional[str] = Field(None, description="Delivery or pickup")
    
    # Classification stage results
    intent: Optional[IntentType] = Field(None, description="Classified intent")
    intent_confidence: Optional[float] = Field(None, description="Intent confidence score")
    
    # Context building results
    conversation_history: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Recent conversation messages"
    )
    relevant_products: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Relevant inventory items"
    )
    lead_info: Optional[Dict[str, Any]] = Field(None, description="Lead information")

    # Agentic reasoning
    agent_scratchpad: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Reasoning history for the current turn"
    )
    
    # Action execution results
    action_type: Optional[str] = Field(None, description="Action that was executed")
    action_result: Optional[Dict[str, Any]] = Field(None, description="Action execution result")
    
    # Response generation
    response_text: Optional[str] = Field(None, description="Generated response")
    response_media_url: Optional[str] = Field(None, description="Media URL for response")
    response_already_sent: bool = Field(
        default=False,
        description="True when the action stage already sent the WhatsApp message (e.g. QR). "
                    "Response generator will skip the send but still persist to DB."
    )
    
    # Metadata
    started_at: datetime = Field(default_factory=datetime.utcnow, description="Pipeline start time")
    current_stage: Optional[PipelineStage] = Field(None, description="Current processing stage")
    error: Optional[str] = Field(None, description="Error message if pipeline fails")
    
    class Config:
        use_enum_values = True


class PipelineResult(BaseModel):
    """Result of pipeline execution."""
    
    success: bool = Field(..., description="Whether pipeline succeeded")
    message_sid: str = Field(..., description="Message SID that was processed")
    response_sent: bool = Field(default=False, description="Whether response was sent")
    response_message_sid: Optional[str] = Field(None, description="Response message SID")
    intent: Optional[str] = Field(None, description="Classified intent")
    action_executed: Optional[str] = Field(None, description="Action executed")
    error: Optional[str] = Field(None, description="Error if failed")
    processing_time_ms: float = Field(..., description="Total processing time")
    
    class Config:
        use_enum_values = True
