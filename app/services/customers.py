"""
Customers (Y-70): the WhatsApp agent finds or creates the business's customer by phone when a purchase
cycle (lead) starts, and links the sale when it is recorded. The panel shows them under Clientes.

The phone is kept as digits only (`whatsapp:+59171234567` → `59171234567`), the same rule as the panel
(`yeison_panel_backend/app/services/customers.py`) and the migration that filled the table.
"""

import re
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer

_NON_DIGITS = re.compile(r"\D")


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    digits = _NON_DIGITS.sub("", str(raw).replace("whatsapp:", ""))
    return digits if 6 <= len(digits) <= 20 else None


def _clean(value: Optional[str], size: int) -> Optional[str]:
    value = " ".join(str(value).split()) if value is not None else ""
    return value[:size] or None


async def ensure_customer(db: AsyncSession, tenant_id: int, phone: Optional[str], name: Optional[str] = None,
                          document_id: Optional[str] = None) -> Optional[int]:
    """The id of the active customer with this phone, created if new (safe if two turns race).
    Fills the name and CI/NIT only where they were empty. None if the phone is not usable."""
    phone = normalize_phone(phone)
    if phone is None:
        return None
    name, document_id = _clean(name, 150), _clean(document_id, 30)
    await db.execute(
        insert(Customer)
        .values(tenant_id=tenant_id, phone=phone, name=name, document_id=document_id, source="whatsapp")
        .on_conflict_do_nothing(index_elements=["tenant_id", "phone"],
                                index_where=and_(Customer.active, Customer.phone.isnot(None)))
    )
    customer = (await db.execute(
        select(Customer).where(Customer.tenant_id == tenant_id, Customer.phone == phone, Customer.active == True)
    )).scalar_one_or_none()
    if customer is None:
        return None
    if name and not customer.name:
        customer.name = name
    if document_id and not customer.document_id:
        customer.document_id = document_id
    return customer.id


async def record_purchase(db: AsyncSession, customer_id: int, sale_date: datetime) -> None:
    """Move `last_purchase_at` forward (never back)."""
    await db.execute(
        update(Customer).where(Customer.id == customer_id)
        .values(last_purchase_at=func.greatest(func.coalesce(Customer.last_purchase_at, sale_date), sale_date),
                updated_at=func.now())
    )
