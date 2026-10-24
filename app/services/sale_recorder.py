"""
Record a confirmed sale in the `sale` table (Y-12).

Called when the supervisor approves an order. Amounts are computed here from
the stored cart (Decimal, never float); the LLM never decides them.
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentInstance, Lead, SalesConversation, Tenant
from app.models.sale import Sale
from app.services.invoice_request import build_invoice_request

logger = structlog.get_logger()

_CENTS = Decimal("0.01")


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def build_sale_items(cart_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize cart lines into the sale_items JSON (unit price and line total as strings)."""
    lines = []
    for item in cart_items:
        raw_quantity = item.get("quantity")
        quantity = 1 if raw_quantity in (None, "") else int(raw_quantity)
        if quantity <= 0:
            continue
        unit_price = _money(item.get("price"))
        lines.append({
            "product_id": item.get("id"),
            "name": item.get("name") or item.get("product_name") or "Producto",
            "quantity": quantity,
            "unit_price": str(unit_price),
            "line_total": str((unit_price * quantity).quantize(_CENTS)),
        })
    return lines


async def record_sale(
    db: AsyncSession,
    conversation: SalesConversation,
    agent: AgentInstance,
    payment_method: str,
) -> Optional[Sale]:
    """
    Create the sale for the conversation's current purchase cycle, once.

    The current lead is the latest one linked to the conversation. Returns the
    existing sale if this cycle was already recorded, or None if there is no
    lead or no items to record.
    """
    lead = (await db.execute(
        select(Lead).where(Lead.conversation_id == conversation.id).order_by(Lead.id.desc()).limit(1)
    )).scalar_one_or_none()
    if not lead:
        logger.error("sale_not_recorded_no_lead", conversation_id=conversation.id)
        return None

    existing = (await db.execute(
        select(Sale).where(Sale.sales_conversation_id == conversation.id, Sale.lead_id == lead.id,
                           Sale.active == True)
    )).scalar_one_or_none()
    if existing:
        return existing

    items = build_sale_items((conversation.cart_contents or {}).get("items") or [])
    if not items:
        logger.error("sale_not_recorded_empty_cart", conversation_id=conversation.id, lead_id=lead.id)
        return None

    subtotal = sum((Decimal(line["line_total"]) for line in items), Decimal("0.00"))
    base_currency = (await db.execute(
        select(Tenant.base_currency).where(Tenant.id == agent.tenant_id)
    )).scalar_one_or_none()

    sale = Sale(
        tenant_id=agent.tenant_id,
        lead_id=lead.id,
        agent_instance_id=agent.id,
        sales_conversation_id=conversation.id,
        sale_date=datetime.utcnow(),
        status="completed",
        subtotal=subtotal,
        # Shelf prices already include taxes; the tax breakdown belongs to accounting (Y-38)
        tax=Decimal("0.00"),
        discount=Decimal("0.00"),
        total_amount=subtotal,
        payment_method=payment_method,
        # QR: the supervisor approved the receipt. Physical: payment is collected on delivery.
        payment_status="paid" if payment_method == "qr" else "pending",
        sale_items=items,
        currency=base_currency,
        invoice_request=build_invoice_request(conversation.cart_contents),
    )
    db.add(sale)
    await db.flush()
    logger.info("sale_recorded", sale_id=sale.id, conversation_id=conversation.id,
                total=str(subtotal), payment_method=payment_method)
    return sale
