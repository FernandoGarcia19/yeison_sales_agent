"""Customers who ask for an invoice (Y-44): detection, the extra checkout fields and what the sale keeps."""

import pytest

from app.schemas.pipeline import PipelineContext
from app.services.invoice_request import (
    INVOICE_NAME_FIELD, INVOICE_TAX_ID_FIELD, build_invoice_request, checkout_requirements, mentions_invoice,
)
from app.services.pipeline.stages.action_executor import ActionExecutorStage


@pytest.mark.parametrize("text", [
    "Quiero factura por favor", "¿Emiten factura?", "con factura, gracias", "necesito que me facturen? mi NIT es 123",
    "Me pueden dar la factura", "FACTURA a nombre de mi empresa",
])
def test_asking_for_an_invoice(text):
    assert mentions_invoice(text) is True


@pytest.mark.parametrize("text", [
    "sin factura", "no necesito factura", "No quiero la factura", "no requiero una factura gracias",
    "ya no me hace falta factura",
])
def test_declining_an_invoice(text):
    assert mentions_invoice(text) is False


@pytest.mark.parametrize("text", ["Hola, buenas tardes", "¿cuánto cuesta la polera?", "", None])
def test_not_mentioning_it(text):
    assert mentions_invoice(text) is None


def test_requirements_only_grow_when_the_customer_asked():
    configured = ["Nombre completo", "Dirección de entrega"]
    assert checkout_requirements(configured, False) == configured
    assert checkout_requirements(None, False) == ["Nombre completo", "Dirección de entrega", "NIT"]
    assert checkout_requirements(configured, True) == configured + [INVOICE_TAX_ID_FIELD, INVOICE_NAME_FIELD]
    assert configured == ["Nombre completo", "Dirección de entrega"]  # the agent's own list is never changed


def test_a_nit_the_business_already_asks_for_is_not_asked_twice():
    assert checkout_requirements(None, True) == ["Nombre completo", "Dirección de entrega", "NIT", INVOICE_NAME_FIELD]
    both = ["Nombre", "NIT", "Razón social"]
    assert checkout_requirements(both, True) == both


def test_the_sale_keeps_what_the_customer_gave():
    assert build_invoice_request({"items": []}) is None
    assert build_invoice_request({"invoice_requested": False, "checkout_data": {"NIT": "1"}}) is None

    cart = {"invoice_requested": True, "checkout_data": {
        "Nombre completo": "Carla M.", INVOICE_TAX_ID_FIELD: " 4567890 ", INVOICE_NAME_FIELD: "Carla Mendoza SRL"}}
    request = build_invoice_request(cart)
    assert request["tax_id"] == "4567890" and request["name"] == "Carla Mendoza SRL" and request["requested_at"]

    plain = build_invoice_request({"invoice_requested": True, "checkout_data": {"Nombre completo": "Carla M.", "NIT": "99"}})
    assert plain["tax_id"] == "99" and plain["name"] == "Carla M."  # no razón social: the customer's name

    empty = build_invoice_request({"invoice_requested": True, "checkout_data": {}})
    assert empty["tax_id"] is None and empty["name"] is None  # still recorded: the owner will ask


def _context(message, **extra):
    return PipelineContext(message_sid="m1", sender_phone="+59170000001", recipient_phone="+59170000009",
                           message_body=message, tenant_id=1, **extra)


@pytest.fixture
def executor(monkeypatch):
    stage = ActionExecutorStage()
    state = {"invoices": True}

    async def invoices(_context):
        return state["invoices"]

    monkeypatch.setattr(stage, "_tenant_invoices", invoices)
    stage.state = state
    return stage


async def test_asking_marks_the_cart_and_adds_the_fields(executor):
    context = _context("quiero factura")
    await executor._note_invoice_request(context)
    assert context.cart_contents["invoice_requested"] is True
    assert executor._get_checkout_requirements(context)[-2:] == ["NIT", INVOICE_NAME_FIELD]


async def test_a_business_that_does_not_invoice_collects_nothing(executor):
    executor.state["invoices"] = False
    context = _context("quiero factura")
    await executor._note_invoice_request(context)
    assert "invoice_requested" not in context.cart_contents
    assert executor._get_checkout_requirements(context) == ["Nombre completo", "Dirección de entrega", "NIT"]


async def test_other_messages_change_nothing(executor):
    context = _context("¿tienen polera negra?", cart_contents={"invoice_requested": True})
    await executor._note_invoice_request(context)
    assert context.cart_contents["invoice_requested"] is True


async def test_changing_their_mind_drops_the_invoice_data_but_keeps_the_rest(executor):
    data = {"Nombre completo": "Carla M.", "NIT": "99", INVOICE_NAME_FIELD: "Carla SRL", INVOICE_TAX_ID_FIELD: "123"}
    context = _context("mejor sin factura", cart_contents={"invoice_requested": True, "checkout_data": dict(data)},
                       checkout_data=dict(data))
    await executor._note_invoice_request(context)
    assert "invoice_requested" not in context.cart_contents
    assert context.checkout_data == {"Nombre completo": "Carla M.", "NIT": "99"}
    assert context.cart_contents["checkout_data"] == {"Nombre completo": "Carla M.", "NIT": "99"}
