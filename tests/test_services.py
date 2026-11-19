"""
Y-103: services and plans without stock, and the product sheet the agent answers with.

What it fixes: a service with quantity 0 never reached the agent (it loaded only products in stock),
the cart capped it at its "stock", the agent said "quedan N" of a haircut, and the checkout asked a
service where to ship it.
"""

from types import SimpleNamespace

import pytest

from app.models.inventory import InventoryTenant, sellable
from app.schemas.pipeline import ConversationState, IntentType, PipelineContext
from app.services import cart, catalog_search, delivery, product_sheet
from app.services.pipeline.stages.response_generator import ResponseGeneratorStage

CUT_SHEET = {
    "fields": [{"key": "duration", "label": "Duración", "value": "45 minutos"},
               {"key": "modality", "label": "Modalidad", "value": "En el local"},
               {"key": "includes", "label": "Qué incluye", "value": "Lavado, corte y secado"},
               {"key": "empty", "label": "Garantía", "value": ""}],
    "faq": [{"q": "¿Atienden a niños?", "a": "Sí, desde los 5 años."}, {"q": "¿Sin respuesta?", "a": ""}],
}
CATALOGUE = [
    {"id": 1, "name": "Chompa de alpaca", "price": "320.00", "quantity": 10, "description": "Tejida", "kind": "product"},
    {"id": 2, "name": "Corte de cabello", "price": "60.00", "quantity": None, "description": "", "kind": "service",
     "duration_minutes": 45, "details": product_sheet.clean(CUT_SHEET)},
    {"id": 3, "name": "Plan Club mensual", "price": "99.00", "quantity": None, "description": "Membresía", "kind": "plan",
     "details": {"fields": [{"key": "period", "label": "Periodicidad", "value": "Mensual"},
                            {"key": "includes", "label": "Qué incluye", "value": "2 ajustes de prendas al mes"}],
                 "faq": [{"q": "¿Puedo cancelar?", "a": "Sí, cuando quieras, sin permanencia."}]}},
    {"id": 4, "name": "Asesoría de imagen", "price": "150.00", "quantity": None, "description": "", "kind": "service",
     "details": {"fields": [{"key": "modality", "label": "Modalidad", "value": "Local, a domicilio o virtual"}], "faq": []}},
]


def row(**values):
    base = dict(id=9, product_name="Corte", price="60.00", quantity=0, description="", track_stock=True,
                kind="product", details={}, duration_minutes=None)
    base.update(values)
    product = SimpleNamespace(**base)
    product.tracks_stock = InventoryTenant.tracks_stock.fget(product)
    return product


# --- the catalogue -----------------------------------------------------------------------------

def test_services_and_untracked_products_are_sellable_with_quantity_zero():
    sql = str(sellable().compile(compile_kwargs={"literal_binds": True}))
    assert "inventory_tenant.track_stock IS false" in sql and "inventory_tenant.kind != 'product'" in sql
    assert "inventory_tenant.quantity > 0" in sql


def test_a_catalogue_item_has_no_stock_when_it_does_not_count_one():
    service = catalog_search.catalogue_item(row(kind="service", quantity=0, duration_minutes=45, details=CUT_SHEET))
    assert service["quantity"] is None and service["kind"] == "service" and service["duration_minutes"] == 45
    # Only the filled fields and questions of the sheet
    assert [f["label"] for f in service["details"]["fields"]] == ["Duración", "Modalidad", "Qué incluye"]
    assert service["details"]["faq"] == [{"q": "¿Atienden a niños?", "a": "Sí, desde los 5 años."}]
    untracked = catalog_search.catalogue_item(row(track_stock=False, quantity=0))
    assert untracked["quantity"] is None and untracked["kind"] == "product" and "details" not in untracked
    tracked = catalog_search.catalogue_item(row(quantity=7))
    assert tracked["quantity"] == 7
    assert catalog_search.catalogue_item(row(kind="algo-raro"))["kind"] == "product"


def test_the_search_reads_the_sheet():
    # «niños» is only in the questions of the haircut's sheet; «cancelar» only in the plan's
    assert catalog_search.rank(CATALOGUE, "¿atienden niños?")[0]["name"] == "Corte de cabello"
    assert catalog_search.rank(CATALOGUE, "¿se puede cancelar cuando quiera?")[0]["name"] == "Plan Club mensual"


def test_only_the_sheets_of_the_turn_reach_the_prompt():
    sample = catalog_search.with_sheets_of_the_turn(catalog_search.rank(CATALOGUE, "hola"), "hola")
    assert not any("details" in p for p in sample)
    named = catalog_search.with_sheets_of_the_turn(catalog_search.rank(CATALOGUE, "corte de cabello"), "corte de cabello")
    assert [p["name"] for p in named if p.get("details")] == ["Corte de cabello"]
    # The original catalogue (cached) keeps its sheets
    assert all("details" in p for p in CATALOGUE[1:])


# --- the cart ------------------------------------------------------------------------------------

def test_the_cart_never_caps_a_service():
    catalogue = CATALOGUE + [{"id": 5, "name": "Ajuste viejo", "price": "20", "quantity": 0, "kind": "service"}]
    items, changes = cart.apply([], [{"op": "add", "id": 2, "quantity": 3}, {"op": "add", "id": 5, "quantity": 2}],
                                catalogue)
    assert {l["name"]: l["quantity"] for l in items} == {"Corte de cabello": 3, "Ajuste viejo": 2}
    assert not any(c["op"] in ("limited_by_stock", "out_of_stock") for c in changes)
    line = items[0]
    assert line["stock"] is None and line["kind"] == "service" and line["modality"] == "En el local"
    text = " ".join(cart.describe_changes(changes))
    assert "hay" not in text and "agotado" not in text


def test_products_still_follow_their_stock():
    items, changes = cart.apply([], [{"op": "add", "id": 1, "quantity": 15}], CATALOGUE)
    assert items[0]["quantity"] == 10 and changes[-1]["op"] == "limited_by_stock" and "kind" not in items[0]


# --- the checkout --------------------------------------------------------------------------------

def _items(*ids):
    return cart.apply([], [{"op": "add", "id": i} for i in ids], CATALOGUE)[0]


CONFIG = {"delivery": delivery.from_operations({"delivery": {"enabled": True}, "pickup": {"enabled": True}})}


def test_only_services_ask_no_delivery():
    # One way to give it (its sheet says "En el local"): only the name
    assert delivery.requirements(CONFIG, {"items": _items(2)}) == [delivery.NAME_FIELD]
    # A plan: only the name
    assert delivery.requirements(CONFIG, {"items": _items(3)}) == [delivery.NAME_FIELD]
    # Local, a domicilio or virtual: the customer chooses
    assert delivery.requirements(CONFIG, {"items": _items(4)}) == [delivery.MODALITY_FIELD, delivery.NAME_FIELD]
    # Even if the customer said "a domicilio" earlier
    assert delivery.ADDRESS_FIELD not in delivery.requirements(CONFIG, {"items": _items(4), "delivery_method": "delivery"})


def test_with_appointments_the_preferred_time_is_asked():
    config = {**CONFIG, "services": delivery.services_config({"industry_answers": {"appointments": True}})}
    assert delivery.requirements(config, {"items": _items(2)}) == [delivery.NAME_FIELD, delivery.PREFERRED_TIME_FIELD]
    # A plan is not an appointment
    assert delivery.requirements(config, {"items": _items(3)}) == [delivery.NAME_FIELD]
    # A mixed cart ships its product and still asks the time of the service
    mixed = delivery.requirements(config, {"items": _items(1, 2)})
    assert mixed[0] == delivery.METHOD_FIELD and mixed[-1] == delivery.PREFERRED_TIME_FIELD


def test_no_home_service_means_no_modality_question():
    config = {**CONFIG, "services": delivery.services_config({"industry_answers": {"home_service": False}})}
    items = cart.apply([], [{"op": "add", "id": 6}], [{"id": 6, "name": "Manicure", "price": "40", "kind": "service"}])[0]
    assert delivery.requirements(config, {"items": items}) == [delivery.NAME_FIELD]
    assert delivery.requirements(CONFIG, {"items": items}) == [delivery.MODALITY_FIELD, delivery.NAME_FIELD]


def test_the_agents_own_list_loses_its_delivery_fields_for_services():
    config = {**CONFIG, "checkout_requirements": ["Nombre completo", "Dirección de entrega", "CI"]}
    assert delivery.requirements(config, {"items": _items(3)}) == ["Nombre completo", "CI"]
    assert delivery.requirements(config, {"items": _items(1)}) == ["Nombre completo", "Dirección de entrega", "CI"]


def test_services_config_from_the_rubro_answers():
    assert delivery.services_config(None) == {"appointments": False, "home_service": None}
    assert delivery.services_config({"industry_answers": {"appointments": True, "home_service": True}}) == {
        "appointments": True, "home_service": True}


@pytest.fixture
def executor(monkeypatch):
    from app.services.pipeline.stages.action_executor import ActionExecutorStage

    stage = ActionExecutorStage()

    async def nothing(context):
        return None

    monkeypatch.setattr(stage, "_note_invoice_request", nothing)
    return stage


async def test_a_service_checkout_keeps_no_delivery_method(executor, monkeypatch):
    async def fake_extract(message, required, collected, context=None):
        assert delivery.METHOD_FIELD not in required and delivery.ADDRESS_FIELD not in required
        return {delivery.MODALITY_FIELD: "a domicilio", delivery.NAME_FIELD: "Ana Rojas"}

    async def fake_payment(context):
        context.action_result = {"action": "physical_payment_initiated"}

    async def no_qr(context):
        return {"qr": False, "physical": True}

    monkeypatch.setattr(executor, "_extract_checkout_fields", fake_extract)
    monkeypatch.setattr(executor, "_handle_physical_payment_flow", fake_payment)
    monkeypatch.setattr(executor, "_get_payment_methods_enabled", no_qr)
    context = PipelineContext(message_sid="m1", sender_phone="+59170000001", recipient_phone="+59170000009",
                              message_body="A domicilio, soy Ana Rojas", conversation_history=[],
                              agent_config={"tenant_info": {"currency": "BOB"}, **CONFIG})
    context.intent, context.current_state = IntentType.UNKNOWN, ConversationState.FULFILLMENT_COORD
    context.cart_contents = {"items": _items(4), "delivery_method": "delivery"}
    await executor.process(context)
    assert "delivery_method" not in context.cart_contents
    assert delivery.METHOD_FIELD not in context.checkout_data
    assert context.action_result["action"] == "physical_payment_initiated"


# --- the reply prompt ----------------------------------------------------------------------------

def test_the_prompt_shows_the_sheet_and_no_stock_for_services():
    block = ResponseGeneratorStage._product_block(CATALOGUE[1], "BOB")
    assert "(Servicio)" in block and "Duración: 45 minutos" in block and "FICHA:" in block
    assert "Qué incluye: Lavado, corte y secado" in block and "P: ¿Atienden a niños? R: Sí, desde los 5 años." in block
    assert "Disponibilidad" not in block and "unidades" not in block
    assert "10 unidades" in ResponseGeneratorStage._product_block(CATALOGUE[0], "BOB")
    untracked = {"id": 7, "name": "Bolso", "price": "10", "quantity": None, "description": ""}
    assert "no se controla stock" in ResponseGeneratorStage._product_block(untracked, "BOB")


def test_a_long_sheet_is_cut():
    long_sheet = {"fields": [{"key": f"k{i}", "label": f"Campo {i}", "value": "x" * 200} for i in range(25)], "faq": []}
    lines = product_sheet.prompt_lines({"details": long_sheet})
    assert 0 < len(lines) < 25 and sum(map(len, lines)) <= 1500


def test_the_user_prompt_has_the_sheet_rule_and_the_service_checkout(monkeypatch):
    stage = ResponseGeneratorStage()
    context = PipelineContext(message_sid="m1", sender_phone="+59170000001", recipient_phone="+59170000009",
                              message_body="¿el corte incluye lavado?", conversation_history=[],
                              agent_config={"tenant_info": {"currency": "BOB"}})
    context.intent = IntentType.PRODUCT_INQUIRY
    context.relevant_products = [CATALOGUE[1]]
    context.action_type = "product_inquiry"
    context.action_result = {"products": [CATALOGUE[1]]}
    prompt = stage._build_user_prompt(context)
    assert "REGLA DE LA FICHA" in prompt and "nunca digas cuántos quedan" in prompt
    assert prompt.count("Lavado, corte y secado") == 1  # not again in the action result

    context.current_state = ConversationState.FULFILLMENT_COORD
    context.agent_config = {**CONFIG, "services": {"appointments": True}}
    context.cart_contents = {"items": _items(2)}
    instructions = stage._state_instructions(context)
    assert "NO preguntes por envío" in instructions and "una persona del equipo" in instructions


# --- what the staging battery showed (2026-11-19) -------------------------------------------------

def test_the_modality_is_read_from_the_message():
    assert delivery.detect_modality("Virtual, a nombre de Ana Rojas") == "Virtual"
    assert delivery.detect_modality("prefiero por videollamada") == "Virtual"
    assert delivery.detect_modality("En el local, soy Ana") == "En el local"
    assert delivery.detect_modality("que vengan a domicilio") == "A domicilio"
    # In a mixed cart «a domicilio» is the delivery of the products
    assert delivery.detect_modality("a domicilio", home_allowed=False) is None
    # Two at once, or none: the agent asks
    assert delivery.detect_modality("virtual o en el local, no sé") is None
    assert delivery.detect_modality("Ana Rojas") is None


def test_a_shortened_field_name_from_the_model_is_accepted():
    fields = [delivery.MODALITY_FIELD, delivery.NAME_FIELD]
    assert delivery.match_field("Modalidad del servicio", fields) == delivery.MODALITY_FIELD
    assert delivery.match_field("modalidad", fields) == delivery.MODALITY_FIELD
    assert delivery.match_field(delivery.NAME_FIELD, fields) == delivery.NAME_FIELD
    assert delivery.match_field("Teléfono", fields) is None


async def test_virtual_and_a_name_close_the_service_checkout(executor, monkeypatch):
    async def model_misses_the_modality(message, required, collected, context=None):
        return {delivery.NAME_FIELD: "Ana Rojas"}

    async def fake_payment(context):
        context.action_result = {"action": "physical_payment_initiated"}

    async def no_qr(context):
        return {"qr": False, "physical": True}

    monkeypatch.setattr(executor, "_extract_checkout_fields", model_misses_the_modality)
    monkeypatch.setattr(executor, "_handle_physical_payment_flow", fake_payment)
    monkeypatch.setattr(executor, "_get_payment_methods_enabled", no_qr)
    context = PipelineContext(message_sid="m1", sender_phone="+59170000001", recipient_phone="+59170000009",
                              message_body="Virtual, a nombre de Ana Rojas", conversation_history=[],
                              agent_config={"tenant_info": {"currency": "BOB"}, **CONFIG})
    context.intent, context.current_state = IntentType.UNKNOWN, ConversationState.FULFILLMENT_COORD
    context.cart_contents = {"items": _items(4)}
    await executor.process(context)
    assert context.checkout_data[delivery.MODALITY_FIELD] == "Virtual"
    assert context.action_result["action"] == "physical_payment_initiated"


def test_a_general_question_gets_only_the_items_it_names():
    plan = catalog_search.named(CATALOGUE, "¿Qué incluye el Plan Club mensual?")
    assert [p["name"] for p in plan] == ["Plan Club mensual"]
    assert catalog_search.with_sheets_of_the_turn(plan, "¿Qué incluye el Plan Club mensual?")[0]["details"]
    assert catalog_search.named(CATALOGUE, "¿A qué hora abren?") == []
