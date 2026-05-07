"""
Tenant model - represents a customer/organization in the multi-tenant system.
"""

from typing import Optional, List
from sqlalchemy import String, Boolean, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, ActiveMixin


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
