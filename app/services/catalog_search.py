"""
Which products of the catalogue the agent works with on this turn.

The agent used to see the first 5 active products, whatever the customer asked: a business with
more could not sell the sixth. Now the whole catalogue is ranked by the words of the message and,
with less weight, of the last messages (so "sí" or "quiero 2" keep the product being discussed).
"""

from typing import Any, Dict, List, Optional

from app.services.purchase_confirmation import normalize

# Products shown to the models on one turn (prompt size)
TOP = 8
# Most products read from the database for one business
MAX_CATALOGUE = 500
_HISTORY_MESSAGES = 4
_STOPWORDS = {
    "que", "con", "por", "para", "los", "las", "una", "uno", "unos", "unas", "del", "como", "cuanto",
    "cuanta", "cuantos", "cuantas", "tienen", "tiene", "tienes", "hay", "quiero", "quisiera", "precio",
    "cuesta", "cuestan", "esta", "estan", "son", "mas", "muy", "hola", "buenas", "buenos", "dias", "tardes",
    "noches", "gracias", "favor", "porfa", "pedido", "pedir", "comprar", "venden", "vende", "disponible",
    "disponibles", "producto", "productos", "algo", "este", "esta", "eso", "esa", "ese", "ustedes", "tambien",
}


def _stem(word: str) -> str:
    # "galletas" ~ "galleta", "pantalones" ~ "pantalon", then "tejida" ~ "tejido", "blanca" ~ "blanco"
    for suffix in ("es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    if len(word) > 4 and word[-1] in "ao":
        word = word[:-1]
    return word


def words(text: Optional[str]) -> set:
    return {_stem(w) for w in normalize(text).split() if len(w) >= 3 and w not in _STOPWORDS}


def rank(products: List[Dict[str, Any]], message: Optional[str],
         history: Optional[List[Dict[str, Any]]] = None, top: int = TOP) -> List[Dict[str, Any]]:
    """The products the conversation is about, best first; a sample of the catalogue if it names none."""
    asked = words(message)
    earlier = set().union(*(words(m.get("content")) for m in (history or [])[-_HISTORY_MESSAGES:])) if history else set()

    scored = []
    for position, product in enumerate(products):
        name = words(product.get("name"))
        description = words(product.get("description"))
        score = 4 * len(name & asked) + len(description & asked) + 2 * len(name & earlier)
        if score:
            scored.append((-score, position, product))
    if not scored:
        return products[:top]
    return [product for _, _, product in sorted(scored, key=lambda item: item[:2])[:top]]
