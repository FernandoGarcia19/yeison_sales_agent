"""Prices as the customer reads them: the business's currency, never a bare "$"."""

from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# Same symbols as the panel (app/services/director.py)
CURRENCY_LABELS = {"BOB": "Bs", "USD": "US$", "PEN": "S/", "ARS": "$", "CLP": "$", "COP": "$", "MXN": "$"}
# Yeison started in Bolivia: a business without base_currency charges in bolivianos
DEFAULT_CURRENCY = "BOB"


def normalize(currency: Optional[str]) -> str:
    return (currency or DEFAULT_CURRENCY).strip().upper() or DEFAULT_CURRENCY


def label(currency: Optional[str]) -> str:
    code = normalize(currency)
    return CURRENCY_LABELS.get(code, code)


def money(amount: Any, currency: Optional[str] = None) -> str:
    """'Bs 8.00'; an amount that is not a number is shown as it came."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return str(amount)
    return f"{label(currency)} {value:.2f}"
