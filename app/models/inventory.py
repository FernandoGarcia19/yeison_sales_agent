"""
InventoryTenant model - represents products available for a tenant.
"""

from typing import TYPE_CHECKING, Any, Dict, Optional
from decimal import Decimal
from sqlalchemy import Boolean, String, Text, BigInteger, ForeignKey, Numeric, Integer, text
from sqlalchemy.dialects.postgresql import JSONB
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
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    track_stock: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # Services and plans (Y-103): what it is, its sheet (fields and questions) and how long a service takes
    kind: Mapped[str] = mapped_column(String(20), nullable=False, server_default="product")
    details: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Relationships
    tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        back_populates="inventory_items"
    )

    def __repr__(self) -> str:
        return f"<InventoryTenant(id={self.id}, product={self.product_name}, qty={self.quantity})>"

    @property
    def is_available(self) -> bool:
        """Check if product is in stock. Untracked items (services, plans, quantity=None) are always available."""
        return self.active and (not self.tracks_stock or self.quantity is None or self.quantity > 0)

    @property
    def tracks_stock(self) -> bool:
        """A service or a plan, or a product the business does not count, has no stock to run out of."""
        return self.track_stock is not False and (self.kind or "product") == "product"


def sellable():
    """What the agent can sell: products in stock, untracked ones, services and plans (they hold no stock)."""
    from sqlalchemy import or_
    return or_(
        InventoryTenant.quantity > 0,
        InventoryTenant.quantity.is_(None),
        InventoryTenant.track_stock.is_(False),
        InventoryTenant.kind != "product",
    )
