"""
«¿Dónde está mi pedido?» (Y-71): the customer's last confirmed sale, as the business left it in the panel.

The business moves each order through `sale.fulfillment_status` (pending → preparing → shipped →
delivered) on the Sales screen of the panel, and may cancel it. When the customer asks for their
order, context_builder loads the last sale of this conversation (or of the same customer in this
business) and the response generator tells it as it is: no invented delivery times.
"""

import re
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.models.lead import Lead
from app.models.sale import Sale

FULFILLMENT_LABELS = {
    "pending": "recibido, todavía por preparar",
    "preparing": "en preparación",
    "shipped": "enviado, va en camino",
    "delivered": "entregado",
}
PAYMENT_LABELS = {"paid": "pagado", "pending": "pago pendiente"}

_ORDER = r"(pedido|orden|compra|paquete|env[ií]o|encargo)"
_ASKS = [re.compile(p, re.I) for p in (
    rf"d[oó]nde\s+(est[aá]|anda|va|qued[oó])\s+(mi|el|nuestro)\s+{_ORDER}",
    rf"(c[oó]mo|qu[eé])\s+(va|est[aá]|pas[oó]|hay)\s+(con\s+)?(mi|el)\s+{_ORDER}",
    rf"estado\s+de\s+(mi|el)\s+{_ORDER}",
    rf"\b(mi|el)\s+{_ORDER}\s+(ya|todav[ií]a|a[uú]n)\b",
    rf"\b(lleg[oóa]|lleg[oó]\s+ya|ha\s+llegado)\s+(mi|el)\s+{_ORDER}",
    r"cu[aá]ndo\s+(me\s+)?(llega|llegar[aá]|lo\s+traen|lo\s+(env[ií]an|mandan|entregan)|me\s+lo\s+(env[ií]an|mandan|traen|entregan))",
    r"\bya\s+(me\s+)?(lo|la|los|las)\s+(enviaron|mandaron|despacharon|entregaron)",
    r"\b(seguimiento|tracking|rastrear)\b",
    r"where\s+is\s+my\s+order|order\s+status",
)]


def asks_for_order(text: Optional[str]) -> bool:
    """True when the customer asks about an order already placed («¿dónde está mi pedido?»)."""
    return bool(text) and any(p.search(text) for p in _ASKS)


def _when(sale_date: Optional[datetime], now: Optional[datetime] = None) -> str:
    if sale_date is None:
        return ""
    days = ((now or datetime.utcnow()).date() - sale_date.date()).days
    if days <= 0:
        return "hoy"
    if days == 1:
        return "ayer"
    return f"hace {days} días"


def describe(sale: Sale, now: Optional[datetime] = None) -> Dict[str, Any]:
    """What the agent may say about the sale: only facts the panel recorded."""
    items = [f"{line.get('quantity')} x {line.get('name') or line.get('product_name') or 'producto'}"
             for line in (sale.sale_items or []) if isinstance(line, dict)]
    cancelled = sale.status == "cancelled"
    return {
        "found": True,
        "number": sale.id,
        "when": _when(sale.sale_date, now),
        "items": items,
        "total": money(sale.total_amount, sale.currency),
        "payment": PAYMENT_LABELS.get(sale.payment_status or "pending", sale.payment_status or ""),
        "status": "anulado" if cancelled else FULFILLMENT_LABELS.get(sale.fulfillment_status or "pending",
                                                                     FULFILLMENT_LABELS["pending"]),
        "cancelled": cancelled,
    }


async def last_order(db: AsyncSession, tenant_id: int, conversation_id: Optional[int]) -> Dict[str, Any]:
    """The last sale of this conversation, or of the same customer in this business (another agent)."""
    customer_id = None
    if conversation_id is not None:
        customer_id = (await db.execute(
            select(Lead.customer_id).where(Lead.conversation_id == conversation_id, Lead.customer_id.is_not(None))
            .order_by(Lead.id.desc()).limit(1)
        )).scalar_one_or_none()
    mine = [Sale.sales_conversation_id == conversation_id] if conversation_id is not None else []
    if customer_id is not None:
        mine.append(Sale.customer_id == customer_id)
    if not mine:
        return {"found": False}
    sale = (await db.execute(
        select(Sale).where(Sale.tenant_id == tenant_id, Sale.active == True, or_(*mine))  # noqa: E712
        .order_by(Sale.sale_date.desc(), Sale.id.desc()).limit(1)
    )).scalar_one_or_none()
    return describe(sale) if sale else {"found": False}


def prompt_block(order: Optional[Dict[str, Any]]) -> str:
    """The lines response_generator adds when the customer asked for their order."""
    if not order:
        return ""
    if not order.get("found"):
        return ("EL CLIENTE PREGUNTA POR SU PEDIDO, pero no tiene pedidos confirmados con nosotros por este chat. "
                "Díselo con amabilidad y ofrece ayudarle a hacer uno, o que una persona del equipo lo revise.\n\n")
    lines = [f"- Pedido #{order['number']} ({order['when']}): " + ", ".join(order["items"]),
             f"- Total: {order['total']} ({order['payment']})",
             f"- Estado: {order['status']}"]
    rule = ("Si el pedido está anulado, dilo y ofrece ayuda. " if order.get("cancelled") else "")
    return ("EL CLIENTE PREGUNTA POR SU PEDIDO. Su último pedido, tal como lo tiene el negocio:\n"
            + "\n".join(lines) + "\n"
            + rule
            + "Dile el estado tal cual. NO inventes fechas, horas de entrega ni números de guía: si quiere más "
              "detalle, dile que el equipo le confirma por este chat.\n\n")
