"""
Bank QR charge - each single-use bank QR the agent sent. Schema owned by yeison_migrations.

The source of truth for a QR payment, apart from the conversation (see app/services/bank_qr.py).
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BankQrCharge(Base):
    __tablename__ = "bank_qr_charge"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alias: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tenant.id"), nullable=False)
    agent_instance_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("agent_instance.id"), nullable=True)
    sales_conversation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("sales_conversation.id"), nullable=True
    )
    lead_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("lead.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # The cart when the QR was sent: items, checkout_data, invoice_requested, delivery_method
    cart: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # pending, replaced (a newer QR was sent; still payable), paid, disabled, expired
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    sale_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("sale.id"), nullable=True)
    # duplicate (paid after the order was already paid) or late (paid after the purchase was dropped)
    review: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    check_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_check_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # {"kind": paid|late|duplicate, "todo": [stock, customer, supervisor], "done": {step: iso}, "attempts": n}
    followups: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
