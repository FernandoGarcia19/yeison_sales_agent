"""
How the business delivers, and what the agent may say and ask about it (Y-58).

The panel and the onboarding write it in `configuration_tenant.operations`:

    delivery: {enabled, zones, cost, time}   # envío: where, how much, how long (free text)
    pickup:   {enabled, address, hours}      # retiro en tienda
    business_hours: "L-S 9 a 19"

Older businesses have `delivery` as one sentence. The agent only states what is written here; for
any other place or cost it says it will check with the team (it used to promise deliveries to any
city). The checkout asks for an address only when the order is delivered.
"""

import re
from typing import Any, Dict, List, Optional

from app.services.invoice_request import checkout_requirements as _with_invoice
from app.services.purchase_confirmation import normalize

PICKUP = "pickup"
DELIVERY = "delivery"

# Asked only when the business offers both
METHOD_FIELD = "Entrega (retiro en tienda o envío)"
NAME_FIELD = "Nombre completo"
ADDRESS_FIELD = "Dirección de entrega"

_PICKUP_WORDS = re.compile(r"\b(retir\w*|recog\w*|recoj\w*|paso a|pasare|paso por|voy a la tienda|en tienda|en la tienda|en el local)\b")
_DELIVERY_WORDS = re.compile(r"\b(envi\w*|deliver\w*|delivery|domicilio|a mi casa|mandar\w*|mandame\w*|traer\w*|traigan|lleven|llevar\w*)\b")


def _text(value: Any) -> str:
    return str(value or "").strip()


def from_operations(operations: Optional[Dict[str, Any]], business_address: Optional[str] = None) -> Dict[str, Any]:
    """The delivery options as the agent uses them. `configured` is False when the business said nothing."""
    ops = operations or {}
    raw_delivery = ops.get("delivery")
    raw_pickup = ops.get("pickup")
    result: Dict[str, Any] = {"configured": False, "pickup": None, "delivery": None, "note": None,
                              "hours": _text(ops.get("business_hours")) or None}

    if isinstance(raw_delivery, str) and raw_delivery.strip():
        # A sentence written by hand (older businesses): the agent can quote it, nothing more
        result.update(configured=True, note=raw_delivery.strip())
    elif isinstance(raw_delivery, dict) and raw_delivery.get("enabled"):
        result.update(configured=True, delivery={
            "zones": _text(raw_delivery.get("zones")) or None,
            "cost": _text(raw_delivery.get("cost")) or None,
            "time": _text(raw_delivery.get("time")) or None,
        })
    if isinstance(raw_pickup, dict) and raw_pickup.get("enabled"):
        result.update(configured=True, pickup={
            "address": _text(raw_pickup.get("address")) or _text(business_address) or None,
            "hours": _text(raw_pickup.get("hours")) or result["hours"],
        })
    if isinstance(raw_delivery, dict) or isinstance(raw_pickup, dict):
        # The business answered the question, even if only to say "no envíos"
        result["configured"] = True
    return result


def methods(options: Dict[str, Any]) -> List[str]:
    """What the customer can choose. Unknown (nothing or only a sentence): both, the customer says."""
    chosen = [m for m in (PICKUP, DELIVERY) if options.get(m)]
    return chosen or [PICKUP, DELIVERY]


def detect_method(message: Optional[str]) -> Optional[str]:
    text = normalize(message)
    pickup = bool(_PICKUP_WORDS.search(text))
    delivery = bool(_DELIVERY_WORDS.search(text))
    if pickup == delivery:
        return None
    return PICKUP if pickup else DELIVERY


def checkout_fields(options: Dict[str, Any], method: Optional[str]) -> List[str]:
    """What to ask before paying, by how the order leaves the shop."""
    available = methods(options)
    if method not in available:
        method = available[0] if len(available) == 1 else None
    fields = [] if method or len(available) == 1 else [METHOD_FIELD]
    fields.append(NAME_FIELD)
    if method == DELIVERY:
        fields.append(ADDRESS_FIELD)
    return fields


def prompt_section(options: Dict[str, Any]) -> str:
    """ENTREGAS for the reply prompt: the facts and the rule not to promise anything else."""
    lines = []
    if options.get("pickup"):
        p = options["pickup"]
        line = "- Retiro en tienda"
        if p.get("address"):
            line += f": {p['address']}"
        if p.get("hours"):
            line += f" (horario: {p['hours']})"
        lines.append(line)
    if options.get("delivery"):
        d = options["delivery"]
        parts = [f"zonas: {d['zones']}" if d.get("zones") else None,
                 f"costo: {d['cost']}" if d.get("cost") else None,
                 f"tiempo: {d['time']}" if d.get("time") else None]
        lines.append("- Envío (" + "; ".join(p for p in parts if p) + ")" if any(parts) else "- Envío")
    if options.get("note"):
        lines.append(f"- {options['note']}")
    if options.get("hours") and not options.get("pickup"):
        lines.append(f"- Horario de atención: {options['hours']}")

    if not options.get("configured"):
        return ("ENTREGAS: el negocio no configuró cómo entrega. NO prometas envíos, zonas, costos ni tiempos: "
                "si preguntan, di que lo confirmas con el equipo.\n")
    head = "ENTREGAS (lo único que puedes afirmar):\n" + "\n".join(lines) + "\n"
    rule = ("Si preguntan por un lugar, costo o tiempo que no figura arriba, NO digas que sí: di que lo consultas "
            "con el equipo y que le avisan por aquí.\n")
    if not options.get("delivery") and not options.get("note"):
        rule += "No hacemos envíos: solo retiro en tienda.\n"
    if not options.get("pickup") and options.get("delivery"):
        rule += "No hay retiro en tienda: solo envío.\n"
    return head + rule


def requirements(agent_config: Optional[Dict[str, Any]], cart_contents: Optional[Dict[str, Any]]) -> List[str]:
    """Checkout fields: the agent's own list if it has one; otherwise by delivery method. Invoice data on top."""
    config = agent_config or {}
    cart = cart_contents or {}
    by_method = checkout_fields(config.get("delivery") or from_operations(None), cart.get("delivery_method"))
    return _with_invoice(config.get("checkout_requirements"), bool(cart.get("invoice_requested")), default=by_method)


def method_label(method: Optional[str]) -> Optional[str]:
    return {PICKUP: "Retiro en tienda", DELIVERY: "Envío"}.get(method)
