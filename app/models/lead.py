"""
Lead model - represents a sales lead/prospect.
"""

from typing import Optional, List, TYPE_CHECKING
from datetime import datetime
from sqlalchemy import String, BigInteger, ForeignKey, Integer, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, ActiveMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant
    from app.models.agent_instance import AgentInstance
    from app.models.conversation import SalesConversation


class Lead(Base, TimestampMixin, ActiveMixin):
    """
    Lead model representing a sales prospect.
    
    Leads are created by the main backend, but this service may
    need to read lead information during conversations.
    
    Note: This service will call the main backend API to CREATE/UPDATE leads,
    not write directly to this table.
    """
    
    __tablename__ = "lead"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_instance_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("agent_instance.id"),
        nullable=True,
        index=True
    )
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tenant.id"),
        nullable=False,
        index=True
    )
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    lead_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)
    last_contact: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    converted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    receipt_object_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    conversation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("sales_conversation.id"),
        nullable=True,
        index=True
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        back_populates="leads"
    )
    agent_instance: Mapped[Optional["AgentInstance"]] = relationship(
        "AgentInstance",
        back_populates="leads"
    )
    conversation: Mapped[Optional["SalesConversation"]] = relationship(
        "SalesConversation",
        foreign_keys=[conversation_id],
        back_populates="leads"
    )
    
    def __repr__(self) -> str:
        return f"<Lead(id={self.id}, name={self.name}, phone={self.phone}, status={self.status})>"
    
    @property
    def is_qualified(self) -> bool:
        """Check if lead is qualified (score >= 70)."""
        return self.score is not None and self.score >= 70
