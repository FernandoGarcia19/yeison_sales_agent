"""
The cart (Y-56): the model proposes operations, the code applies them. What the staging test chat
showed on 2026-10-29: "agrégame una gorra", "mejor que sean 3", "quita la gorra" and "cancela"
changed nothing while the agent said they did; 15 chompas with 10 in stock; a product that does not
exist swapped for another one.
"""

import pytest

from app.schemas.pipeline import ConversationState, IntentType, PipelineContext
from app.services import cart

CATALOGUE = [
    {"id": 14, "name": "Chompa de alpaca", "price": "320.00", "quantity": 10, "description": "Tejida"},
    {"id": 15, "name": "Gorra bordada Illimani", "price": "60.00", "quantity": 23, "description": ""},
    {"id": 19, "name": "Medias pack x3", "price": "35.00", "quantity": 58, "description": ""},
    {"id": 30, "name": "Bolso de aguayo", "price": "140.00", "quantity": None, "description": "Sin control de stock"},
    {"id": 31, "name": "Polera edición limitada", "price": "90.00", "quantity": 0, "description": ""},
]


def lines(items):
    return {line["name"]: line["quantity"] for line in items}


def test_add_set_and_remove():
    items, changes = cart.apply([], [{"op": "add", "id": 19, "quantity": 2}], CATALOGUE)
    assert lines(items) == {"Medias pack x3": 2} and changes == [{"op": "added", "name": "Medias pack x3", "quantity": 2}]

    items, _ = cart.apply(items, [{"op": "add", "id": 15}], CATALOGUE)
    assert lines(items) == {"Medias pack x3": 2, "Gorra bordada Illimani": 1}

    items, changes = cart.apply(items, [{"op": "set", "id": 19, "quantity": 3}], CATALOGUE)
    assert lines(items)["Medias pack x3"] == 3 and changes == [{"op": "quantity", "name": "Medias pack x3", "from": 2, "to": 3}]

    items, changes = cart.apply(items, [{"op": "remove", "id": 15}], CATALOGUE)
    assert lines(items) == {"Medias pack x3": 3} and changes == [{"op": "removed", "name": "Gorra bordada Illimani"}]


def test_adding_again_adds_up_and_set_zero_removes():
    items, _ = cart.apply([], [{"op": "add", "id": 19, "quantity": 2}], CATALOGUE)
    items, _ = cart.apply(items, [{"op": "add", "id": 19, "quantity": 1}], CATALOGUE)
    assert lines(items) == {"Medias pack x3": 3}
    items, changes = cart.apply(items, [{"op": "set", "id": 19, "quantity": 0}], CATALOGUE)
    assert items == [] and changes[0]["op"] == "removed"


def test_stock_limits_the_quantity():
    items, changes = cart.apply([], [{"op": "add", "id": 14, "quantity": 15}], CATALOGUE)
    assert lines(items) == {"Chompa de alpaca": 10}
    assert {"op": "limited_by_stock", "name": "Chompa de alpaca", "requested": 15, "available": 10} in changes
    # Untracked stock: no limit
    items, _ = cart.apply([], [{"op": "add", "id": 30, "quantity": 40}], CATALOGUE)
    assert lines(items) == {"Bolso de aguayo": 40}
    # Sold out: nothing added
    items, changes = cart.apply([], [{"op": "add", "id": 31, "quantity": 1}], CATALOGUE)
    assert items == [] and changes == [{"op": "out_of_stock", "name": "Polera edición limitada", "requested": 1}]


def test_old_lines_without_stock_are_not_capped_at_their_quantity():
    old = [{"id": 19, "name": "Medias pack x3", "price": "35.00", "quantity": 2}]
    items, _ = cart.apply(old, [{"op": "set", "id": 19, "quantity": 5}], CATALOGUE)
    assert lines(items) == {"Medias pack x3": 5}


def test_unknown_products_are_reported_not_swapped():
    items, changes = cart.apply([], [{"op": "add", "id": None, "name": "polera de seda roja", "quantity": 2},
                                     {"op": "add", "id": 999, "name": "inventado"}], CATALOGUE)
    assert items == []
    assert [c["op"] for c in changes] == ["not_found", "not_found"]
    _, changes = cart.apply([], [{"op": "remove", "id": 15, "name": "gorra"}], CATALOGUE)
    assert changes == [{"op": "not_in_cart", "name": "gorra"}]


def test_bad_operations_are_ignored():
    items, changes = cart.apply([], [{"op": "explode", "id": 19}, {"op": "add", "id": 19, "quantity": "dos"}], CATALOGUE)
    assert lines(items) == {"Medias pack x3": 1} and len(changes) == 1


def test_summary_and_description():
    items, changes = cart.apply([], [{"op": "add", "id": 19, "quantity": 2}, {"op": "add", "id": 14, "quantity": 15}], CATALOGUE)
    summary = cart.summary(items, "BOB")
    assert summary == {"lines": ["2 x Medias pack x3 — Bs 70.00", "10 x Chompa de alpaca — Bs 3200.00"], "total": "Bs 3270.00"}
    text = cart.describe_changes(changes)
    assert "Se agregó 2 x Medias pack x3." in text and any("hay 10" in t for t in text)


@pytest.mark.parametrize("message, edits", [
    ("Agrégame también una gorra", True), ("quita la gorra", True), ("Mejor que sean 3", True),
    ("Mejor cancela el pedido", True), ("ya no quiero las medias", True), ("y una chompa", True),
    ("Luis Mamani, Av. 6 de Agosto 100, NIT 4455667", False), ("Sí", False), ("", False),
])
def test_might_edit(message, edits):
    assert cart.might_edit(message, CATALOGUE) is edits


# --- the executor -----------------------------------------------------------------------------

def _context(message, state, items=None):
    context = PipelineContext(message_sid="m1", sender_phone="+59170000001", recipient_phone="+59170000009",
                              message_body=message, agent_config={"tenant_info": {"currency": "BOB"}})
    context.intent, context.current_state = IntentType.UNKNOWN, state
    context.relevant_products = CATALOGUE
    context.cart_contents = {"items": [cart.line_from_product(CATALOGUE[2], 2)] if items is None else items}
    context.checkout_data = {"Nombre completo": "Luis"}
    context.cart_contents["checkout_data"] = context.checkout_data
    return context


@pytest.fixture
def executor(monkeypatch):
    from app.services.pipeline.stages.action_executor import ActionExecutorStage

    stage = ActionExecutorStage()
    stage.extracted = {"operations": [], "cancel": False}

    async def fake_extract(context):
        return stage.extracted

    async def nothing(context):
        return None

    monkeypatch.setattr(stage, "_extract_cart_operations", fake_extract)
    monkeypatch.setattr(stage, "_note_invoice_request", nothing)
    return stage


async def test_adding_during_checkout_keeps_collecting_data(executor):
    executor.extracted = {"operations": [{"op": "add", "id": 15, "quantity": 1}], "cancel": False}
    context = await executor.process(_context("Agrégame también una gorra Illimani", ConversationState.FULFILLMENT_COORD))
    assert lines(context.cart_contents["items"]) == {"Medias pack x3": 2, "Gorra bordada Illimani": 1}
    result = context.action_result
    assert result["action"] == "cart_updated" and result["next"] == "collect_checkout_data"
    assert result["cart"]["total"] == "Bs 130.00" and "Nombre completo" not in result["missing_fields"]
    assert context.current_state == ConversationState.FULFILLMENT_COORD


async def test_cancel_empties_the_cart(executor):
    executor.extracted = {"operations": [], "cancel": True}
    context = await executor.process(_context("Mejor cancela el pedido", ConversationState.FULFILLMENT_COORD))
    assert context.action_result == {"action": "order_cancelled"}
    assert context.current_state == ConversationState.BROWSING
    assert not context.cart_contents.get("items") and context.checkout_data == {}


async def test_removing_the_last_product_leaves_browsing(executor):
    executor.extracted = {"operations": [{"op": "remove", "id": 19}], "cancel": False}
    context = await executor.process(_context("quita las medias", ConversationState.CART_BUILDING))
    assert context.action_result["next"] == "cart_empty" and context.current_state == ConversationState.BROWSING


async def test_a_message_that_changes_nothing_goes_on(executor, monkeypatch):
    calls = []

    async def fake_checkout(context):
        calls.append(context.message_body)
        context.action_result = {"action": "collect_checkout_data"}

    monkeypatch.setattr(executor, "_handle_checkout_collection", fake_checkout)
    context = await executor.process(_context("las medias son para regalo", ConversationState.FULFILLMENT_COORD))
    assert calls == ["las medias son para regalo"] and lines(context.cart_contents["items"]) == {"Medias pack x3": 2}


async def test_unconfirmed_interest_builds_the_cart(executor):
    executor.extracted = {"operations": [{"op": "add", "id": 14, "quantity": 1}], "cancel": False}
    context = _context("Me interesa una chompa de alpaca", ConversationState.BROWSING, items=[])
    context.intent = IntentType.PURCHASE_INTENT
    await executor.process(context)
    assert context.current_state == ConversationState.CART_BUILDING
    assert context.action_result["action"] == "qualify_lead" and context.action_result["cart"]["total"] == "Bs 320.00"


async def test_a_product_not_in_the_catalogue_is_not_bought(executor):
    executor.extracted = {"operations": [{"op": "add", "id": None, "name": "polera de seda roja", "quantity": 2}], "cancel": False}
    context = _context("Quiero 2 poleras de seda roja", ConversationState.BROWSING, items=[])
    context.intent = IntentType.PURCHASE_INTENT
    await executor.process(context)
    assert context.action_result["missing"] == "product" and not context.cart_contents.get("items")
    assert "no está en nuestro catálogo" in context.action_result["cart_changes"][0]
    assert context.current_state == ConversationState.BROWSING
