"""
SalesConversation model - represents an ongoing conversation with a customer.
"""

import enum
from typing import TYPE_CHECKING, List, Dict, Any, Optional
from sqlalchemy import String, BigInteger, ForeignKey, JSON
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, ActiveMixin

if TYPE_CHECKING:
    from app.models.agent_instance import AgentInstance
    from app.models.lead import Lead


class ConversationState(str, enum.Enum):
    BROWSING = "browsing"
    CART_BUILDING = "cart_building"
    FULFILLMENT_COORD = "fulfillment_coord"
    AWAITING_RECEIPT = "awaiting_receipt"
    ORDER_COMPLETED = "order_completed"
    PAUSED = "paused"


class SalesConversation(Base, TimestampMixin, ActiveMixin):
    """
    SalesConversation model representing an ongoing conversation.
    
    Messages are stored as JSONB in the 'messages' field for flexibility.
    Each message contains: role, content, timestamp, intent, etc.
    
    The external_user_id represents the WhatsApp user (could be lead_id
    or a unique identifier for the customer).
    """
    
    __tablename__ = "sales_conversation"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_instance_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agent_instance.id"),
        nullable=False,
        index=True
    )
    external_user_id: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="WhatsApp phone number in E.164 format (e.g., +584129876543)"
    )
    lead_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("lead.id"),
        nullable=True,
        index=True
    )
    messages: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=list
    )
    current_state: Mapped[ConversationState] = mapped_column(
        SQLEnum(ConversationState, name="conversation_state_enum", create_type=False),
        nullable=False,
        default=ConversationState.BROWSING
    )
    cart_contents: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict
    )
    fulfillment_type: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="pickup, delivery, or null"
    )
    
    # Relationships
    agent_instance: Mapped["AgentInstance"] = relationship(
        "AgentInstance",
        back_populates="conversations"
    )
    lead: Mapped[Optional["Lead"]] = relationship(
        "Lead",
        back_populates="conversations",
        foreign_keys=[lead_id]
    )
    
    def __repr__(self) -> str:
        msg_count = len(self.messages) if self.messages else 0
        return f"<SalesConversation(id={self.id}, agent_id={self.agent_instance_id}, messages={msg_count})>"
    
    def add_message(self, role: str, content: str, **msg_metadata) -> None:
        """Add a new message to the conversation."""
        from datetime import datetime
        
        if self.messages is None:
            self.messages = []
        
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
            **msg_metadata
        }
        self.messages.append(message)
    
    def get_last_message(self) -> Dict[str, Any] | None:
        """Get the most recent message."""
        if not self.messages:
            return None
        return self.messages[-1]
    
    def get_conversation_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get the last N messages for context."""
        if not self.messages:
            return []
        return self.messages[-limit:]
    
    @property
    def message_count(self) -> int:
        """Return the total number of messages."""
        return len(self.messages) if self.messages else 0
