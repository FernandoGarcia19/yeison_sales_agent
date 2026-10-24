"""
Customers who ask for an invoice (Y-44).

Yeison does not issue invoices: the business does. When a customer asks for one, the agent only collects
the data it needs (NIT or CI and razón social) as two more checkout fields, and the sale keeps that request
(`sale.invoice_request`) so the owner can issue the invoice and write its number down in the panel.
A customer who does not ask is never asked for these data.
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

INVOICE_TAX_ID_FIELD = "NIT o CI para la factura"
INVOICE_NAME_FIELD = "Razón social para la factura"
DEFAULT_CHECKOUT = ["Nombre completo", "Dirección de entrega", "NIT"]

_MENTION = re.compile(r"\bfactur\w*", re.I)
# "sin factura", "no necesito factura", "no quiero la factura"...
_DECLINE = re.compile(
    r"\b(?:sin|ni)\s+(?:la\s+|una\s+|su\s+)?factur\w*"
    r"|\bno\s+(?:me\s+)?(?:quiero|necesito|requiero|ocupo|pido|hace\s+falta|hacen\s+falta|es\s+necesari[oa])"
    r"\s+(?:la\s+|una\s+|su\s+)?factur\w*", re.I)
_TAX_ID_KEY = re.compile(r"\b(?:nit|ci)\b|carnet", re.I)
_NAME_KEY = re.compile(r"raz[oó]n\s+social", re.I)


def mentions_invoice(text: Optional[str]) -> Optional[bool]:
    """True: the customer asks for an invoice. False: says they do not want one. None: does not mention it."""
    if not text or not _MENTION.search(text):
        return None
    return False if _DECLINE.search(text) else True


def is_invoice_field(name: str) -> bool:
    return name in (INVOICE_TAX_ID_FIELD, INVOICE_NAME_FIELD)


def checkout_requirements(configured: Optional[List[str]], invoice_requested: bool) -> List[str]:
    """The fields to collect before paying: the agent's own, plus the invoice data when the customer asked."""
    fields = list(configured) if isinstance(configured, list) and configured else list(DEFAULT_CHECKOUT)
    if not invoice_requested:
        return fields
    if not any(_TAX_ID_KEY.search(f) for f in fields):
        fields.append(INVOICE_TAX_ID_FIELD)
    if not any(_NAME_KEY.search(f) for f in fields):
        fields.append(INVOICE_NAME_FIELD)
    return fields


def _pick(data: Dict[str, Any], pattern: "re.Pattern[str]") -> Optional[str]:
    for key, value in data.items():
        if pattern.search(str(key)) and str(value or "").strip():
            return str(value).strip()
    return None


def build_invoice_request(cart_contents: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """What goes into `sale.invoice_request`; None when the customer did not ask for an invoice."""
    cart = cart_contents or {}
    if not cart.get("invoice_requested"):
        return None
    data = cart.get("checkout_data") or {}
    return {
        "tax_id": _pick(data, _TAX_ID_KEY),
        "name": _pick(data, _NAME_KEY) or _pick(data, re.compile(r"nombre", re.I)),
        "requested_at": datetime.utcnow().isoformat(timespec="seconds"),
    }
