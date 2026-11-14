"""
Sale model - a confirmed sale. Schema owned by yeison_migrations.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Sale(Base):
    __tablename__ = "sale"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tenant.id"), nullable=False)
    lead_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("lead.id"), nullable=False)
    agent_instance_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agent_instance.id"), nullable=False)
    sales_conversation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("sales_conversation.id"), nullable=True
    )
    sale_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    tax: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    discount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    payment_method: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    payment_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="pending")
    sale_items: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, nullable=False)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    exchange_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8), nullable=True)
    # Y-44: the customer asked for an invoice: {tax_id, name, requested_at}
    invoice_request: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    # Bank QR: alias of the paid QR (unique, so a payment is never recorded twice)
    payment_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
