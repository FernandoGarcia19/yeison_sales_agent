"""
SalesConversation model - represents an ongoing conversation with a customer.
"""

from typing import TYPE_CHECKING, List, Dict, Any
from sqlalchemy import String, BigInteger, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, ActiveMixin

if TYPE_CHECKING:
    from app.models.agent_instance import AgentInstance


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
    external_user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="WhatsApp user identifier or lead_id"
    )
    messages: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=list
    )
    
    # Relationships
    agent_instance: Mapped["AgentInstance"] = relationship(
        "AgentInstance",
        back_populates="conversations"
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
