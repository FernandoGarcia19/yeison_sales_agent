"""
Y-58: what the staging test chat showed: the agent promised deliveries to Cochabamba that the business
never set up, always asked for an address and a NIT (even to pick up in the shop), kept answering
after the customer asked for a person, and kept a half-built cart forever.
"""

from datetime import datetime, timedelta

import pytest

from app.schemas.pipeline import ConversationState, IntentType, PipelineContext
from app.services import delivery, handoff
from app.services.pipeline.stages.response_generator import ResponseGeneratorStage

ONBOARDING_OPS = {
    "delivery": {"enabled": True, "zones": "La Paz y El Alto", "cost": "Bs 15", "time": "el mismo día"},
    "pickup": {"enabled": True},
    "business_hours": "L-S 9 a 19",
}


# --- delivery options ----------------------------------------------------------------------------

def test_options_from_the_onboarding():
    options = delivery.from_operations(ONBOARDING_OPS, business_address="Calle Sagárnaga 123, La Paz")
    assert options["configured"] and delivery.methods(options) == ["pickup", "delivery"]
    assert options["pickup"] == {"address": "Calle Sagárnaga 123, La Paz", "hours": "L-S 9 a 19"}
    section = delivery.prompt_section(options)
    assert "La Paz y El Alto" in section and "Bs 15" in section and "Calle Sagárnaga 123" in section
    assert "NO empieces con «sí»" in section


def test_only_pickup_says_there_is_no_delivery():
    options = delivery.from_operations({"delivery": {"enabled": False}, "pickup": {"enabled": True, "address": "Av. Arce 1"}})
    assert delivery.methods(options) == ["pickup"]
    assert "No hacemos envíos" in delivery.prompt_section(options)


def test_a_sentence_from_older_businesses_is_quoted():
    options = delivery.from_operations({"delivery": "Delivery en La Paz el mismo día"})
    assert options["configured"] and options["note"] == "Delivery en La Paz el mismo día"
    assert delivery.methods(options) == ["pickup", "delivery"]
    assert "Delivery en La Paz el mismo día" in delivery.prompt_section(options)


def test_nothing_configured_promises_nothing():
    options = delivery.from_operations(None)
    assert not options["configured"]
    assert "NO prometas envíos" in delivery.prompt_section(options)


@pytest.mark.parametrize("message, method", [
    ("paso a recogerlo a la tienda", "pickup"), ("Lo retiro mañana", "pickup"), ("retiro en tienda", "pickup"),
    ("envíamelo a mi casa", "delivery"), ("¿hacen delivery?", "delivery"), ("me lo pueden mandar?", "delivery"),
    ("hola", None), ("¿retiro o envío?", None),
])
def test_detect_method(message, method):
    assert delivery.detect_method(message) == method


def test_checkout_fields_by_method():
    both = delivery.from_operations(ONBOARDING_OPS)
    assert delivery.checkout_fields(both, None) == [delivery.METHOD_FIELD, "Nombre completo"]
    assert delivery.checkout_fields(both, "pickup") == ["Nombre completo"]
    assert delivery.checkout_fields(both, "delivery") == ["Nombre completo", "Dirección de entrega"]
    only_delivery = delivery.from_operations({"delivery": {"enabled": True}})
    assert delivery.checkout_fields(only_delivery, None) == ["Nombre completo", "Dirección de entrega"]
    # A method the business does not offer is ignored
    only_pickup = delivery.from_operations({"pickup": {"enabled": True}})
    assert delivery.checkout_fields(only_pickup, "delivery") == ["Nombre completo"]


def test_requirements_keep_the_agents_own_list_and_add_the_invoice():
    config = {"delivery": delivery.from_operations(ONBOARDING_OPS)}
    assert delivery.requirements(config, {"delivery_method": "pickup"}) == ["Nombre completo"]
    with_invoice = delivery.requirements(config, {"delivery_method": "pickup", "invoice_requested": True})
    assert with_invoice[0] == "Nombre completo" and len(with_invoice) == 3
    assert delivery.requirements({**config, "checkout_requirements": ["Nombre", "CI"]}, {}) == ["Nombre", "CI"]


# --- the executor ----------------------------------------------------------------------------------

def _context(message, state=ConversationState.BROWSING, history=None, **config):
    context = PipelineContext(message_sid="m1", sender_phone="+59170000001", recipient_phone="+59170000009",
                              message_body=message, conversation_history=history or [],
                              agent_config={"tenant_info": {"currency": "BOB"},
                                            "delivery": delivery.from_operations(ONBOARDING_OPS), **config})
    context.intent, context.current_state = IntentType.UNKNOWN, state
    return context


@pytest.fixture
def executor(monkeypatch):
    from app.services import notification_service, telegram_service
    from app.services.pipeline.stages.action_executor import ActionExecutorStage

    stage = ActionExecutorStage()
    stage.sent = []

    async def nothing(context):
        return None

    async def fake_whatsapp_notice(**kwargs):
        stage.sent.append(("whatsapp", kwargs["customer_phone"]))
        return True

    async def fake_telegram_notice(chat_id, data):
        stage.sent.append(("telegram", data["conversation_id"]))

    monkeypatch.setattr(stage, "_note_invoice_request", nothing)
    monkeypatch.setattr(notification_service.NotificationService, "notify_handoff_request", fake_whatsapp_notice)
    monkeypatch.setattr(telegram_service, "send_handoff_notification", fake_telegram_notice)
    return stage


async def test_pickup_in_the_checkout_does_not_ask_an_address(executor, monkeypatch):
    async def fake_extract(message, required, collected, context=None):
        return {delivery.METHOD_FIELD: "paso a recoger", "Nombre completo": "Ana Rojas"}

    async def fake_payment(context):
        context.action_result = {"action": "physical_payment_initiated"}

    monkeypatch.setattr(executor, "_extract_checkout_fields", fake_extract)
    monkeypatch.setattr(executor, "_handle_physical_payment_flow", fake_payment)

    async def no_qr(context):
        return {"qr": False, "physical": True}

    monkeypatch.setattr(executor, "_get_payment_methods_enabled", no_qr)
    context = _context("Paso a recoger, soy Ana Rojas", state=ConversationState.FULFILLMENT_COORD)
    context.cart_contents = {"items": [{"id": 19, "name": "Medias pack x3", "price": "35", "quantity": 2}]}
    await executor.process(context)
    assert context.cart_contents["delivery_method"] == "pickup"
    assert context.checkout_data[delivery.METHOD_FIELD] == "Retiro en tienda"
    assert context.action_result["action"] == "physical_payment_initiated"


async def test_delivery_in_the_checkout_asks_the_address(executor, monkeypatch):
    async def fake_extract(message, required, collected, context=None):
        return {"Nombre completo": "Ana Rojas"}

    monkeypatch.setattr(executor, "_extract_checkout_fields", fake_extract)
    context = _context("Envíamelo a mi casa, soy Ana Rojas", state=ConversationState.FULFILLMENT_COORD)
    context.cart_contents = {"items": [{"id": 19, "name": "Medias pack x3", "price": "35", "quantity": 2}]}
    await executor.process(context)
    assert context.cart_contents["delivery_method"] == "delivery"
    assert context.action_result["missing_fields"] == ["Dirección de entrega"]


async def test_asking_for_a_person_pauses_the_agent(executor):
    context = _context("Quiero hablar con una persona", state=ConversationState.FULFILLMENT_COORD,
                       integrations={"supervisor_number": "+59100009625",
                                     "telegram": {"enabled": True, "supervisor_chat_id": "42"}})
    context.conversation_id = 77
    context.cart_contents = {"items": [{"id": 19, "name": "Medias pack x3", "price": "35", "quantity": 2}]}
    from app.core.config import settings
    original = settings.telegram_bot_token
    settings.telegram_bot_token = "t"
    try:
        await executor.process(context)
    finally:
        settings.telegram_bot_token = original
    assert context.current_state == ConversationState.PAUSED
    assert context.action_result == {"action": "handoff", "notified": True}
    assert ("whatsapp", "+59170000001") in executor.sent and ("telegram", "77") in executor.sent
    # The cart waits for the person, untouched
    assert context.cart_contents["items"] and context.cart_contents["state_before_pause"] == "fulfillment_coord"


async def test_a_state_stored_as_text_also_pauses(executor):
    # Conversations loaded from the database (and the test chat) carry the state as plain text
    context = _context("Quiero hablar con una persona")
    context.current_state = "fulfillment_coord"
    await executor.process(context)
    assert context.current_state == ConversationState.PAUSED
    assert context.cart_contents["state_before_pause"] == "fulfillment_coord"


async def test_the_business_keywords_also_pause(executor):
    context = _context("necesito al encargado", conversation_settings={"handoff_to_human_keywords": ["encargado"]})
    await executor.process(context)
    assert context.current_state == ConversationState.PAUSED


async def test_a_paused_conversation_gets_no_reply_until_it_ends(executor):
    context = _context("¿siguen ahí?", state=ConversationState.PAUSED)
    handoff.pause(context.cart_contents, "cart_building", hours=1)
    await executor.process(context)
    assert context.action_result == {"action": "paused"} and context.response_already_sent and not context.response_text

    expired = _context("hola de nuevo", state=ConversationState.PAUSED)
    expired.cart_contents = {"state_before_pause": "browsing",
                             "paused_until": (datetime.utcnow() - timedelta(minutes=1)).isoformat()}
    await executor.process(expired)
    assert expired.current_state == ConversationState.BROWSING and "paused_until" not in expired.cart_contents
    assert expired.action_result.get("action") != "paused"


async def test_a_stale_cart_is_dropped(executor):
    old = (datetime.utcnow() - timedelta(days=2)).isoformat()
    context = _context("hola", state=ConversationState.FULFILLMENT_COORD,
                       history=[{"role": "assistant", "content": "¿Tu nombre?", "timestamp": old}])
    context.cart_contents = {"items": [{"id": 19, "name": "Medias pack x3", "price": "35", "quantity": 2}]}
    await executor.process(context)
    assert context.current_state == ConversationState.BROWSING and not context.cart_contents.get("items")
    assert context.action_result["previous_order_discarded"] is True

    recent = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
    fresh = _context("hola", state=ConversationState.CART_BUILDING,
                     history=[{"role": "assistant", "content": "¿Algo más?", "timestamp": recent}])
    fresh.cart_contents = {"items": [{"id": 19, "name": "Medias pack x3", "price": "35", "quantity": 2}]}
    await executor.process(fresh)
    assert fresh.cart_contents.get("items")


def test_handoff_phrases():
    assert handoff.asks_for_person("Quiero hablar con una persona, por favor")
    assert handoff.asks_for_person("pásame con un asesor")
    assert handoff.asks_for_person("el ENCARGADO por favor", ["encargado"])
    assert not handoff.asks_for_person("quiero 2 medias")


# --- the reply prompt ----------------------------------------------------------------------------

def test_the_prompt_has_the_delivery_rules_and_the_handoff_reply():
    context = _context("¿Hacen envíos a Cochabamba?")
    stage = ResponseGeneratorStage()
    system = stage._build_system_prompt(context)
    assert "ENTREGAS (lo único que puedes afirmar)" in system and "La Paz y El Alto" in system

    context.action_type, context.action_result = "handoff_request", {"action": "handoff", "notified": True}
    assert "una persona del equipo le escribirá" in stage._build_user_prompt(context)

    context.action_type, context.action_result = "pricing_inquiry", {"products": []}
    assert "¿Te preparo 2?" in stage._build_user_prompt(context)


async def test_resume_from_telegram(monkeypatch):
    from app.api.v1 import telegram_webhook
    from app.services import handoff as handoff_module

    said = []

    async def fake_resume(conversation_id):
        return "+59170000001" if conversation_id == 77 else None

    async def fake_send(chat_id, text, reply_markup=None):
        said.append(text)

    monkeypatch.setattr(handoff_module, "resume_conversation", fake_resume)
    monkeypatch.setattr(telegram_webhook, "_send_telegram_message", fake_send)
    await telegram_webhook._resume_agent("resume_agent_77", "42")
    await telegram_webhook._resume_agent("resume_agent_78", "42")
    assert said == ["▶️ El agente volvió a atender a +59170000001.", "Esa conversación ya no estaba en pausa."]
