"""
InventoryTenant model - represents products available for a tenant.
"""

from typing import TYPE_CHECKING
from decimal import Decimal
from sqlalchemy import String, BigInteger, ForeignKey, Numeric, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, ActiveMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class InventoryTenant(Base, TimestampMixin, ActiveMixin):
    """
    InventoryTenant model representing product inventory for a tenant.
    
    This table stores product information that the AI agent can query
    to answer customer questions about availability, pricing, etc.
    """
    
    __tablename__ = "inventory_tenant"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tenant.id"),
        nullable=False,
        index=True
    )
    product_name: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    
    # Relationships
    tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        back_populates="inventory_items"
    )
    
    def __repr__(self) -> str:
        return f"<InventoryTenant(id={self.id}, product={self.product_name}, qty={self.quantity})>"
    
    @property
    def is_available(self) -> bool:
        """Check if product is in stock."""
        return self.active and self.quantity > 0
    
    @property
    def formatted_price(self) -> str:
        """Return formatted price string."""
        return f"${self.price:.2f}"
