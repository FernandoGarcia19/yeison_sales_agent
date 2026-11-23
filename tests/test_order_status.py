"""«¿Dónde está mi pedido?» (Y-71): when the agent looks up the last sale and what it may say."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import order_status


@pytest.mark.parametrize("text", [
    "Hola, ¿dónde está mi pedido?",
    "como va mi pedido",
    "¿Qué pasó con mi compra?",
    "quiero saber el estado de mi pedido",
    "mi pedido todavía no llega",
    "¿Cuándo me llega?",
    "ya lo enviaron?",
    "¿Llegó mi paquete?",
])
def test_questions_about_an_order(text):
    assert order_status.asks_for_order(text)


@pytest.mark.parametrize("text", [
    "Quiero hacer un pedido",
    "Quiero 2 packs de medias",
    "¿Hacen envíos a El Alto?",
    "Gracias, eso es todo",
    "",
    None,
])
def test_other_messages_do_not_look_up_orders(text):
    assert not order_status.asks_for_order(text)


def _sale(**fields):
    base = dict(id=41, status="completed", fulfillment_status="shipped", payment_status="paid",
                total_amount=Decimal("45.00"), currency="BOB", sale_date=datetime(2026, 11, 21, 15, 0),
                sale_items=[{"name": "Medias pack x3", "quantity": 2}, {"product_name": "Gorra", "quantity": 1}])
    base.update(fields)
    return SimpleNamespace(**base)


def test_describe_says_only_what_the_panel_recorded():
    order = order_status.describe(_sale(), now=datetime(2026, 11, 23, 12, 0))
    assert order == {"found": True, "number": 41, "when": "hace 2 días",
                     "items": ["2 x Medias pack x3", "1 x Gorra"], "total": "Bs 45.00", "payment": "pagado",
                     "status": "enviado, va en camino", "cancelled": False}
    block = order_status.prompt_block(order)
    assert "Pedido #41" in block and "enviado, va en camino" in block and "NO inventes" in block
    assert "$" not in block


def test_a_cancelled_sale_is_told_as_cancelled():
    order = order_status.describe(_sale(status="cancelled"), now=datetime(2026, 11, 21, 18, 0))
    assert order["status"] == "anulado" and order["cancelled"] and order["when"] == "hoy"
    assert "anulado" in order_status.prompt_block(order)


def test_no_sale_is_said_plainly():
    assert "no tiene pedidos confirmados" in order_status.prompt_block({"found": False})
    assert order_status.prompt_block(None) == ""


def test_the_checkout_data_is_kept_with_the_sale():
    from app.services.sale_recorder import build_delivery

    cart = {"delivery_method": "delivery",
            "checkout_data": {"Nombre completo": "Luis Mamani", "Dirección de entrega": " Av. 6 de Agosto 100 ",
                              "Referencia": "", "Teléfono": None}}
    assert build_delivery(cart) == {"method": "delivery", "data": {"Nombre completo": "Luis Mamani",
                                                                   "Dirección de entrega": "Av. 6 de Agosto 100"}}
    assert build_delivery({}) is None
