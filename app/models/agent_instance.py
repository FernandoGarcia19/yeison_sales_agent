"""
AgentInstance model - represents a configured AI agent for a tenant.
"""

from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, BigInteger, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, ActiveMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant
    from app.models.conversation import SalesConversation
    from app.models.lead import Lead


class AgentInstance(Base, TimestampMixin, ActiveMixin):
    """
    AgentInstance model representing a configured AI sales agent.
    
    Each tenant can have multiple agent instances, each with different
    configurations (personality, prompts, workflows) stored in the
    configuration JSONB field.
    
    The phone_number field is used to identify which agent should handle
    incoming WhatsApp messages.
    """
    
    __tablename__ = "agent_instance"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tenant.id"),
        nullable=False,
        index=True
    )
    phone_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="WhatsApp number in E.164 format (e.g., +584121234567)"
    )
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False)
    
    # Relationships
    tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        back_populates="agent_instances"
    )
    conversations: Mapped[List["SalesConversation"]] = relationship(
        "SalesConversation",
        back_populates="agent_instance",
        lazy="selectin"
    )
    leads: Mapped[List["Lead"]] = relationship(
        "Lead",
        back_populates="agent_instance",
        lazy="selectin"
    )
    
    def __repr__(self) -> str:
        return f"<AgentInstance(id={self.id}, phone={self.phone_number}, type={self.agent_type})>"
    
    def get_config_value(self, key: str, default=None):
        """Helper to safely get configuration values."""
        return self.configuration.get(key, default)
