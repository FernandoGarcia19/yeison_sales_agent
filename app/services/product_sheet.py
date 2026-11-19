"""
Services, plans and the product sheet (Y-103).

A catalogue item is a `product` (with stock, unless the business does not count it), a `service`
(a haircut, a consultation: no stock) or a `plan` (a monthly membership: no stock). Each one may
carry a sheet the owner fills in the panel (`inventory_tenant.details`):

    {"fields": [{"key": "duration", "label": "Duración", "value": "45 minutos"}, ...],
     "faq": [{"q": "¿Incluye lavado?", "a": "Sí, lavado y secado."}]}

The agent answers about an item only with its sheet; whatever is not there, it checks with the team.
Only the sheets of the items the customer is talking about go into the prompt (tokens).
"""

import re
from typing import Any, Dict, Iterable, List, Optional

PRODUCT = "product"
SERVICE = "service"
PLAN = "plan"
KINDS = (PRODUCT, SERVICE, PLAN)

# What the prompt receives of one sheet at most (the panel caps a sheet at ~4.000 characters)
_MAX_PROMPT_CHARS = 1500
# The sheet field that says how a service is given (local, a domicilio, virtual)
MODALITY_KEYS = ("modality", "modalidad")
_SEVERAL = re.compile(r",|/|\by\b|\bo\b", re.I)


def kind(item: Optional[Dict[str, Any]]) -> str:
    value = str((item or {}).get("kind") or PRODUCT).lower()
    return value if value in KINDS else PRODUCT


def is_service_like(item: Optional[Dict[str, Any]]) -> bool:
    """A service or a plan: nothing to ship, nothing to run out of."""
    return kind(item) in (SERVICE, PLAN)


def only_services(items: Iterable[Dict[str, Any]]) -> bool:
    """A cart of services and plans only (a mixed cart still ships its products)."""
    items = list(items or [])
    return bool(items) and all(is_service_like(i) for i in items)


def has_services(items: Iterable[Dict[str, Any]]) -> bool:
    return any(kind(i) == SERVICE for i in items or [])


def _text(value: Any) -> str:
    return str(value or "").strip()


def clean(details: Any) -> Dict[str, List[Dict[str, str]]]:
    """The sheet with only filled fields and questions (whatever the column holds)."""
    details = details if isinstance(details, dict) else {}
    fields = []
    for f in details.get("fields") or []:
        if isinstance(f, dict) and _text(f.get("value")):
            fields.append({"key": _text(f.get("key")), "label": _text(f.get("label")) or _text(f.get("key")),
                           "value": _text(f.get("value"))})
    faq = []
    for item in details.get("faq") or []:
        if isinstance(item, dict) and _text(item.get("q")) and _text(item.get("a")):
            faq.append({"q": _text(item.get("q")), "a": _text(item.get("a"))})
    return {"fields": fields, "faq": faq}


def is_empty(details: Any) -> bool:
    sheet = clean(details)
    return not sheet["fields"] and not sheet["faq"]


def field(details: Any, keys: Iterable[str]) -> Optional[str]:
    wanted = {k.lower() for k in keys}
    for f in clean(details)["fields"]:
        if f["key"].lower() in wanted or f["label"].lower() in wanted:
            return f["value"]
    return None


def text(details: Any) -> str:
    """The words of the sheet for the catalogue search: values and questions. Not the field labels («Qué
    incluye», «Duración»), which every sheet has: «¿qué incluye el plan?» must not find every service."""
    sheet = clean(details)
    parts = [f["value"] for f in sheet["fields"]]
    parts += [f"{q['q']} {q['a']}" for q in sheet["faq"]]
    return " ".join(parts)


def prompt_lines(item: Dict[str, Any]) -> List[str]:
    """The sheet as the reply prompt shows it, cut at a safe length."""
    sheet = clean(item.get("details"))
    lines = [f"{f['label']}: {f['value']}" for f in sheet["fields"]]
    lines += [f"P: {q['q']} R: {q['a']}" for q in sheet["faq"]]
    out, used = [], 0
    for line in lines:
        if used + len(line) > _MAX_PROMPT_CHARS:
            break
        out.append(line)
        used += len(line)
    return out


def kind_label(item: Dict[str, Any]) -> Optional[str]:
    return {SERVICE: "Servicio", PLAN: "Plan o membresía"}.get(kind(item))


def modality(item: Dict[str, Any]) -> Optional[str]:
    """How a service is given, if its sheet says so."""
    return field(item.get("details"), MODALITY_KEYS) or _text(item.get("modality")) or None


def single_modality(items: Iterable[Dict[str, Any]], home_service: Optional[bool] = None) -> Optional[str]:
    """The one way the services of a cart are given, when there is no choice to make; None to ask."""
    values = {m.lower(): m for m in (modality(i) for i in items if kind(i) == SERVICE) if m}
    if len(values) == 1:
        value = next(iter(values.values()))
        return None if _SEVERAL.search(value) else value
    if not values and home_service is False:
        return "En el local"
    return None
