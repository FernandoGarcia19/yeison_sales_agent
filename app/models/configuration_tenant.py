"""
ConfigurationTenant model - stores business-level configuration for a tenant.
"""

from typing import Optional, TYPE_CHECKING
from sqlalchemy import String, BigInteger, ForeignKey, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class ConfigurationTenant(Base, TimestampMixin):
    """
    ConfigurationTenant model representing business-level configuration.
    
    This table stores tenant-wide business information that is shared across
    all agent instances belonging to the tenant. Configuration is stored in
    JSONB fields for flexibility.
    
    JSONB Field Structure:
    - business: Company information (name, industry, size, location, etc.)
    - contact: Contact details (name, role, email, phone)
    - products: Product-level settings (USPs, target audience, payment methods)
    - operations: Operational settings (sales process, FAQs, hours, objections, etc.)
    """
    
    __tablename__ = "configuration_tenant"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tenant.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    business: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    contact: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    products: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    operations: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    
    # Relationship
    tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        back_populates="configuration"
    )
    
    def __repr__(self) -> str:
        return f"<ConfigurationTenant(id={self.id}, tenant_id={self.tenant_id}, completed={self.is_completed})>"
    
    def get_business_value(self, key: str, default=None):
        """Helper to safely get business configuration values."""
        if not self.business:
            return default
        return self.business.get(key, default)
    
    def get_contact_value(self, key: str, default=None):
        """Helper to safely get contact configuration values."""
        if not self.contact:
            return default
        return self.contact.get(key, default)
    
    def get_products_value(self, key: str, default=None):
        """Helper to safely get products configuration values."""
        if not self.products:
            return default
        return self.products.get(key, default)
    
    def get_operations_value(self, key: str, default=None):
        """Helper to safely get operations configuration values."""
        if not self.operations:
            return default
        return self.operations.get(key, default)
    
    def is_business_info_complete(self) -> bool:
        """Check if minimum business information is provided."""
        if not self.business:
            return False
        required_fields = ["company_name", "industry", "description"]
        return all(self.business.get(field) for field in required_fields)
    
    def is_contact_info_complete(self) -> bool:
        """Check if minimum contact information is provided."""
        if not self.contact:
            return False
        # Phone is required for WhatsApp-only communication
        return bool(self.contact.get("contact_phone"))
    
    def is_qr_payment_enabled(self) -> bool:
        """Check if QR payment is enabled for this tenant."""
        if not self.products:
            return False
        return bool(self.products.get("qr_payment_enabled", False))

    def get_qr_object_key(self) -> str:
        """Get the R2 object key for the QR code image."""
        if not self.products:
            return ""
        return self.products.get("qr_object_key", "")

    def get_qr_payment_url(self) -> str:
        """Legacy fallback: raw URL stored directly. Prefer get_qr_object_key()."""
        if not self.products:
            return ""
        return self.products.get("qr_payment_url", "")
