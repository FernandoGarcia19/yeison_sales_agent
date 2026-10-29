"""The agent works with the products the conversation is about, not the first 5 of the catalogue."""

from app.services import catalog_search

CATALOGUE = [
    {"id": i, "name": name, "price": "10", "quantity": 5, "description": description}
    for i, (name, description) in enumerate([
        ("Polera básica", "Algodón, varias tallas"),
        ("Ibuprofeno 400 mg", "Caja de 10 tabletas"),
        ("Paracetamol 500 mg", "Caja de 20 tabletas"),
        ("Jarabe para la tos", "Frasco de 120 ml"),
        ("Alcohol en gel", "Botella de 250 ml"),
        ("Galletas de quinua", "Paquete de 200 g, sin gluten"),
        ("Pantalón jean", "Corte recto"),
        ("Gorra de lana", "Tejida a mano"),
        ("Medias deportivas", "Pack de 3 pares"),
        ("Chompa de alpaca", "Tejida, talla única"),
    ], start=1)
]


def names(products):
    return [p["name"] for p in products]


def test_a_product_beyond_the_first_five():
    assert names(catalog_search.rank(CATALOGUE, "¿Tienen galletas de quinua?"))[0] == "Galletas de quinua"
    assert names(catalog_search.rank(CATALOGUE, "precio de la chompa"))[0] == "Chompa de alpaca"
    assert names(catalog_search.rank(CATALOGUE, "quiero 2 pantalones"))[0] == "Pantalón jean"


def test_description_words_count_less_than_the_name():
    ranked = names(catalog_search.rank(CATALOGUE, "algo tejido de lana"))
    assert ranked[0] == "Gorra de lana" and "Chompa de alpaca" in ranked


def test_the_conversation_keeps_the_product():
    history = [{"role": "user", "content": "¿A cuánto la chompa de alpaca?"},
               {"role": "assistant", "content": "La *chompa de alpaca* está a Bs 10.00. ¿Te la preparo?"}]
    assert names(catalog_search.rank(CATALOGUE, "Sí", history))[0] == "Chompa de alpaca"
    # What the customer names now wins over what was said before
    assert names(catalog_search.rank(CATALOGUE, "mejor unas medias", history))[0] == "Medias deportivas"


def test_no_product_named_gives_a_sample():
    ranked = catalog_search.rank(CATALOGUE, "¿Qué productos tienen?")
    assert ranked == CATALOGUE[:catalog_search.TOP]
    assert catalog_search.rank([], "galletas") == []


def test_accents_and_plurals():
    assert catalog_search.words("Galletas, PANTALONES y jarabes") == {"gallet", "pantalon", "jarab"}
    assert catalog_search.words("tejido") == catalog_search.words("Tejidas")
