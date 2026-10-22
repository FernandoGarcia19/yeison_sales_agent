"""
Tenant model - represents a customer/organization in the multi-tenant system.
"""

from typing import TYPE_CHECKING, Optional, List
from sqlalchemy import String, Boolean, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, ActiveMixin

if TYPE_CHECKING:  # only for the relationship annotations; the mappers resolve the names
    from app.models.agent_instance import AgentInstance
    from app.models.configuration_tenant import ConfigurationTenant
    from app.models.inventory import InventoryTenant
    from app.models.lead import Lead


class Tenant(Base, TimestampMixin, ActiveMixin):
    """
    Tenant model representing a customer/organization.
    
    This table is managed by the main backend.
    This service primarily performs READ operations.
    """
    
    __tablename__ = "tenant"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    provider_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Business profile (Y-30), managed by panel_backend
    base_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    
    # Relationships (for query convenience)
    agent_instances: Mapped[List["AgentInstance"]] = relationship(
        "AgentInstance",
        back_populates="tenant",
        lazy="selectin"
    )
    inventory_items: Mapped[List["InventoryTenant"]] = relationship(
        "InventoryTenant",
        back_populates="tenant",
        lazy="selectin"
    )
    leads: Mapped[List["Lead"]] = relationship(
        "Lead",
        back_populates="tenant",
        lazy="selectin"
    )
    configuration: Mapped[Optional["ConfigurationTenant"]] = relationship(
        "ConfigurationTenant",
        back_populates="tenant",
        lazy="selectin",
        uselist=False
    )
    
    def __repr__(self) -> str:
        return f"<Tenant(id={self.id}, name={self.name}, email={self.email})>"
