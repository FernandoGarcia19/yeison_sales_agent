"""
When a customer's message closes the purchase.

Customers write "sí, confírmalo", "dale", "quiero pagar" or "me llevo 2"; a list of exact words
("confirmo", "lo quiero") missed most of them, and the agent kept saying "confirmado" while the
cart stayed empty. Accents and punctuation are ignored.
"""

import re
import unicodedata
from typing import Any, Dict, List, Optional

_QUANTITY = r"(\d+|un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|docena|media)"

# Phrases that close a purchase on their own (the classifier already said "purchase_intent")
_CLOSING = re.compile(
    r"\bconfirm\w*"                                   # confirmo, confirmar, confirmalo, confirmado
    r"|\bcompr(o|ar|amos)\b|\bacepto\b"
    r"|\b(lo|la|los|las) (quiero|llevo|compro)\b"
    r"|\bme (lo |la |los |las )?llevo\b"
    r"|\b(quiero|voy a|vamos a) (pagar|hacer el pago|realizar el pago|hacer (el|un|mi) pedido)\b"
    r"|\bpagar ahora\b|\b(realizar|hacer) el pago\b|\bproced\w*"
    r"|\b(hagamos|haz|hazme|haga|hagame) (el|un|mi) pedido\b"
    r"|\bpedido de " + _QUANTITY + r"\b"
    r"|\b(enviamelo|enviamela|enviamelos|enviamelas|mandamelo|mandamela|mandamelos|mandamelas)\b"
    r"|\b(quiero|quisiera|dame|deme|mandame|ponme|anotame|separame|reservame)( me)? " + _QUANTITY + r"\b"
)

# A short reply that says yes. Only closes the purchase when the agent had just offered it.
_YES = {"si", "sip", "dale", "ok", "okay", "okey", "oki", "listo", "claro", "perfecto", "va", "vale",
        "bueno", "correcto", "exacto", "acuerdo", "eso", "afirmativo", "obvio"}
_FILLER = {"de", "es", "asi", "por", "favor", "porfa", "gracias", "nomas", "todo", "ya", "entonces",
           "muy", "bien", "y", "que", "lo", "esta", "seria", "senor", "senora", "hermano", "amigo", "amiga"}
# The agent proposed the order and waits for the customer's confirmation (qualify_lead)
_ORDER_OFFER = "purchase_intent"
# Answers after which a "yes" means "go ahead" only if the agent asked something
_QUESTION_ACTIONS = {"pricing_inquiry", "product_inquiry", "availability_check"}


def normalize(text: Optional[str]) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def closes_purchase(message: Optional[str]) -> bool:
    return bool(_CLOSING.search(normalize(message)))


def is_affirmative(message: Optional[str]) -> bool:
    words = normalize(message).split()
    if not words or len(words) > 6:
        return False
    # "siii", "okkk"
    words = [re.sub(r"(.)\1+$", r"\1", w) for w in words]
    return any(w in _YES for w in words) and all(w in _YES or w in _FILLER for w in words)


def purchase_was_offered(history: Optional[List[Dict[str, Any]]]) -> bool:
    """The agent's last message offered the order or asked whether to go ahead."""
    last = next((m for m in reversed(history or []) if m.get("role") == "assistant"), None)
    if not last:
        return False
    action = last.get("action_type")
    # The model does not always end the offer with a question mark ("te lo dejo listo para confirmar")
    if action == _ORDER_OFFER:
        return True
    return action in _QUESTION_ACTIONS and "?" in (last.get("content") or "")


def confirms_offer(message: Optional[str], history: Optional[List[Dict[str, Any]]]) -> bool:
    return is_affirmative(message) and purchase_was_offered(history)
