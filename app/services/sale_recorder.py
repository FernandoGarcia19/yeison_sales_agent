"""
Record a confirmed sale in the `sale` table (Y-12).

Called when the supervisor approves an order, or when the bank confirms a QR payment (bank_qr). Amounts are computed here from
the stored cart (Decimal, never float); the LLM never decides them.
"""

import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentInstance, Lead, SalesConversation, Tenant
from app.models.sale import Sale
from app.services import customers
from app.services.invoice_request import _NAME_KEY, build_invoice_request

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


def build_delivery(cart_contents: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What the customer gave at checkout, kept with the sale for the panel's Sales screen (Y-71)."""
    data = {str(k): str(v).strip() for k, v in (cart_contents.get("checkout_data") or {}).items()
            if v is not None and str(v).strip()}
    method = cart_contents.get("delivery_method")
    if not data and not method:
        return None
    return {"method": method, "data": data}


async def record_sale(
    db: AsyncSession,
    conversation: SalesConversation,
    agent: AgentInstance,
    payment_method: str,
    payment_reference: Optional[str] = None,
    lead: Optional[Lead] = None,
    cart: Optional[Dict[str, Any]] = None,
) -> Optional[Sale]:
    """
    Create the sale for the conversation's current purchase cycle, once.

    The current lead is the latest one linked to the conversation. Returns the
    existing sale if this cycle was already recorded, or None if there is no
    lead or no items to record.
    """
    # A bank QR paid after the cart changed (or after the purchase was dropped) records the cart and
    # the lead of the moment the QR was sent: that is what the customer paid for.
    if lead is None:
        lead = (await db.execute(
            select(Lead).where(Lead.conversation_id == conversation.id).order_by(Lead.id.desc()).limit(1)
        )).scalar_one_or_none()
    cart_contents = cart if cart is not None else (conversation.cart_contents or {})
    if not lead:
        logger.error("sale_not_recorded_no_lead", conversation_id=conversation.id)
        return None

    existing = (await db.execute(
        select(Sale).where(Sale.sales_conversation_id == conversation.id, Sale.lead_id == lead.id,
                           Sale.active == True)
    )).scalar_one_or_none()
    if existing:
        return existing

    items = build_sale_items(cart_contents.get("items") or [])
    if not items:
        logger.error("sale_not_recorded_empty_cart", conversation_id=conversation.id, lead_id=lead.id)
        return None

    subtotal = sum((Decimal(line["line_total"]) for line in items), Decimal("0.00"))
    base_currency = (await db.execute(
        select(Tenant.base_currency).where(Tenant.id == agent.tenant_id)
    )).scalar_one_or_none()

    invoice = build_invoice_request(cart_contents)
    checkout = cart_contents.get("checkout_data") or {}
    checkout_name = next((str(v).strip() for k, v in checkout.items()
                          if re.search(r"nombre|name", str(k), re.I) and not _NAME_KEY.search(str(k))
                          and str(v or "").strip()), None)
    # Also fills the name and CI/NIT given at checkout where the customer had none
    customer_id = await customers.ensure_customer(
        db, agent.tenant_id, lead.phone or conversation.external_user_id, checkout_name or lead.name,
        (invoice or {}).get("tax_id")) or lead.customer_id
    if customer_id and not lead.customer_id:
        lead.customer_id = customer_id

    sale = Sale(
        tenant_id=agent.tenant_id,
        lead_id=lead.id,
        customer_id=customer_id,
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
        invoice_request=invoice,
        payment_reference=payment_reference,
        delivery=build_delivery(cart_contents),
    )
    db.add(sale)
    await db.flush()
    if customer_id:
        await customers.record_purchase(db, customer_id, sale.sale_date)
    logger.info("sale_recorded", sale_id=sale.id, conversation_id=conversation.id,
                total=str(subtotal), payment_method=payment_method)
    return sale
