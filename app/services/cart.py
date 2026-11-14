"""
The customer's cart (Y-56).

The model only proposes operations ("add 1 gorra", "set medias to 3", "remove gorra", "cancel");
this module applies them against the catalogue, so the cart never holds a product that does not
exist or more units than there are. Every change is reported back (`changes`) and the reply may
only claim what is there.

A cart line is the catalogue product with the units ordered in `quantity` and the stock in `stock`
(None = the business does not count it).
"""

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from app.core.money import money
from app.services.catalog_search import words
from app.services.purchase_confirmation import normalize

# Words of a message that changes the cart (checked without accents)
_EDIT_WORDS = {
    "agrega", "agregame", "agregar", "agregale", "anade", "anademe", "anadir", "suma", "sumame", "tambien",
    "ademas", "otro", "otra", "otros", "otras", "mas", "quita", "quitame", "quitar", "quitale", "saca", "sacame",
    "elimina", "eliminar", "borra", "borrar", "cambia", "cambiame", "cambiar", "mejor", "sean", "solo", "menos",
    "cancela", "cancelar", "cancelo", "cancelado", "olvida", "olvidalo", "anula", "anular",
}
_EDIT_PHRASES = ("ya no", "no quiero", "en vez de", "en lugar de")


def _as_int(value: Any) -> Optional[int]:
    return None if value in (None, "") else int(value)


def line_from_product(product: Dict[str, Any], quantity: int) -> Dict[str, Any]:
    """A cart line from a catalogue product (whose `quantity` is the stock)."""
    return {
        "id": product.get("id"),
        "name": product.get("name") or product.get("product_name") or "Producto",
        "price": str(product.get("price", "0")),
        "quantity": quantity,
        "stock": product.get("quantity"),
    }


def might_edit(message: Optional[str], products: List[Dict[str, Any]]) -> bool:
    """Cheap check before asking the model: edit words, or a product of the catalogue or the cart named."""
    text = normalize(message)
    if not text:
        return False
    if any(phrase in text for phrase in _EDIT_PHRASES) or set(text.split()) & _EDIT_WORDS:
        return True
    named = words(message)
    return any(named & words(p.get("name") or p.get("product_name")) for p in products)


def apply(items: List[Dict[str, Any]], operations: List[Dict[str, Any]],
          catalogue: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """The new cart lines and what changed. Unknown products and missing stock are reported, not added."""
    cart = [dict(line) for line in items]
    known = {str(p.get("id")): p for p in catalogue}
    changes: List[Dict[str, Any]] = []

    for op in operations or []:
        kind = op.get("op")
        pid = str(op.get("id")) if op.get("id") not in (None, "") else None
        try:
            quantity = int(op.get("quantity") if op.get("quantity") not in (None, "") else 1)
        except (TypeError, ValueError):
            quantity = 1
        line = next((l for l in cart if pid is not None and str(l.get("id")) == pid), None)
        product = known.get(pid) if pid else None

        if kind == "remove":
            if line:
                cart.remove(line)
                changes.append({"op": "removed", "name": line["name"]})
            else:
                changes.append({"op": "not_in_cart", "name": op.get("name") or (product or {}).get("name")})
            continue
        if kind not in ("add", "set"):
            continue
        if not line and not product:
            changes.append({"op": "not_found", "name": op.get("name")})
            continue
        if kind == "set" and quantity <= 0:
            if line:
                cart.remove(line)
                changes.append({"op": "removed", "name": line["name"]})
            continue

        wanted = quantity if kind == "set" or not line else int(line["quantity"]) + quantity
        # The catalogue has today's stock (its `quantity`); a line keeps what it had when added
        stock = _as_int(product.get("quantity")) if product else _as_int(line.get("stock"))
        applied = wanted if stock is None else min(wanted, max(stock, 0))
        name = line["name"] if line else (product.get("name") or product.get("product_name"))
        if applied <= 0:
            changes.append({"op": "out_of_stock", "name": name, "requested": wanted})
            continue
        if line:
            before = int(line["quantity"])
            line["quantity"] = applied
            if applied != before:
                changes.append({"op": "quantity", "name": name, "from": before, "to": applied})
        else:
            cart.append(line_from_product(product, applied))
            changes.append({"op": "added", "name": name, "quantity": applied})
        if applied < wanted:
            changes.append({"op": "limited_by_stock", "name": name, "requested": wanted, "available": applied})
    return cart, changes


def total(items: List[Dict[str, Any]]) -> Decimal:
    result = Decimal("0")
    for line in items:
        try:
            result += Decimal(str(line.get("price") or 0)) * int(line.get("quantity") or 0)
        except (InvalidOperation, ValueError):
            continue
    return result


def summary(items: List[Dict[str, Any]], currency: Optional[str]) -> Dict[str, Any]:
    """The cart as the reply shows it: '2 x Medias pack x3 — Bs 70.00' and the total."""
    lines = [
        f"{int(l['quantity'])} x {l['name']} — {money(Decimal(str(l.get('price') or 0)) * int(l['quantity']), currency)}"
        for l in items
    ]
    return {"lines": lines, "total": money(total(items), currency)}


def describe_changes(changes: List[Dict[str, Any]]) -> List[str]:
    """The changes in plain Spanish, for the reply prompt."""
    text = []
    for c in changes:
        if c["op"] == "added":
            text.append(f"Se agregó {c['quantity']} x {c['name']}.")
        elif c["op"] == "quantity":
            text.append(f"{c['name']}: de {c['from']} a {c['to']}.")
        elif c["op"] == "removed":
            text.append(f"Se quitó {c['name']}.")
        elif c["op"] == "not_in_cart":
            text.append(f"{c.get('name') or 'Ese producto'} no estaba en el pedido: no se quitó nada.")
        elif c["op"] == "not_found":
            text.append(f"«{c.get('name') or 'Ese producto'}» no está en nuestro catálogo: no se agregó.")
        elif c["op"] == "out_of_stock":
            text.append(f"{c['name']} está agotado: no se agregó.")
        elif c["op"] == "limited_by_stock":
            text.append(f"De {c['name']} pidió {c['requested']} pero hay {c['available']}: quedaron {c['available']}.")
    return text
