"""
What the chat test showed on staging (2026-10-29): "Sí, confírmalo" or "quiero pagar" never closed
the purchase, prices went out as "$8.00" and the replies carried Markdown that WhatsApp prints as is.
"""

import pytest

from app.core.money import money
from app.integrations.whatsapp.formatting import to_whatsapp
from app.schemas.pipeline import ConversationState, IntentType, PipelineContext
from app.services import purchase_confirmation as pc
from app.services.pipeline.stages.context_builder import ContextBuilderStage
from app.services.pipeline.stages.response_generator import ResponseGeneratorStage

OFFER = [
    {"role": "user", "content": "Quiero 2 galletas de quinua"},
    {"role": "assistant", "content": "Serían 2 galletas de quinua, total Bs 16.00. ¿Confirmas el pedido?",
     "action_type": "purchase_intent"},
]


@pytest.mark.parametrize("message", [
    "Sí, confírmalo", "confirmo", "CONFIRMADO!", "Eso nomás, quiero pagar", "Quiero 2 paquetes",
    "me llevo dos", "Lo quiero", "las llevo", "hagamos el pedido", "dame 3 por favor", "procede",
    "quiero hacer el pedido", "envíamelo", "¿me harías un pedido de 3?", "quisiera 2",
])
def test_closing_phrases(message):
    assert pc.closes_purchase(message)


@pytest.mark.parametrize("message", [
    "¿Tienen galletas de quinua?", "¿a cuánto?", "hola", "¿hacen envíos a Santa Cruz?", "Sí", "gracias",
])
def test_not_closing_by_themselves(message):
    assert not pc.closes_purchase(message)


@pytest.mark.parametrize("message", ["Sí", "siii", "Dale", "ok", "Sí, por favor", "de acuerdo", "Listo, gracias", "sí, eso nomás"])
def test_short_yes(message):
    assert pc.is_affirmative(message)


@pytest.mark.parametrize("message", ["", "no", "sí pero ¿tienen en otro sabor?", "ya veré", "gracias", "sí, ¿y cuánto cuesta el envío a Sucre?"])
def test_not_a_yes(message):
    assert not pc.is_affirmative(message)


def test_yes_counts_only_after_an_offer():
    assert pc.confirms_offer("Sí", OFFER)
    # The order offer counts even without a question mark
    assert pc.confirms_offer("Sí", [{**OFFER[1], "content": "Te lo dejo listo para confirmar el pedido ✨"}])
    # A price answer that did not ask anything
    statement = [{**OFFER[1], "action_type": "pricing_inquiry", "content": "Tenemos galletas de quinua a Bs 8.00."}]
    assert not pc.confirms_offer("Sí", statement)
    assert pc.confirms_offer("Sí", [{**statement[0], "content": "Están a Bs 8.00. ¿Te preparo un paquete?"}])
    # A question that was not about the order (payment or checkout steps answer their own way)
    other = [{**OFFER[1], "action_type": "collect_checkout_data"}]
    assert not pc.confirms_offer("Sí", other)
    assert not pc.confirms_offer("Sí", [])


def _context(message, state=ConversationState.BROWSING, intent=IntentType.UNKNOWN, history=OFFER):
    context = PipelineContext(message_sid="m1", sender_phone="+59170000001", recipient_phone="+59170000009",
                              message_body=message, conversation_history=history,
                              agent_config={"tenant_info": {"currency": "BOB"}})
    # Assigned like the stages do (the constructor would store the plain string)
    context.intent, context.current_state = intent, state
    return context


@pytest.fixture
def executor(monkeypatch):
    from app.services.pipeline.stages.action_executor import ActionExecutorStage

    stage = ActionExecutorStage()
    stage.purchases = []

    async def nothing(context):
        return None

    async def fake_purchase(context):
        stage.purchases.append(context.message_body)

    monkeypatch.setattr(stage, "_note_invoice_request", nothing)
    monkeypatch.setattr(stage, "_handle_purchase_intent", fake_purchase)
    return stage


async def test_a_yes_to_the_offer_goes_to_the_purchase(executor):
    context = await executor.process(_context("Sí"))
    assert executor.purchases == ["Sí"] and context.intent == IntentType.PURCHASE_INTENT


async def test_a_yes_without_an_offer_is_a_normal_message(executor):
    await executor.process(_context("Sí", history=[]))
    await executor.process(_context("Sí", state=ConversationState.ORDER_COMPLETED))
    assert executor.purchases == []


async def test_confirmation_does_not_reopen_a_finished_order():
    from app.services.pipeline.stages.action_executor import ActionExecutorStage

    stage = ActionExecutorStage()
    assert await stage._is_purchase_confirmed(_context("Sí, confírmalo"))
    assert await stage._is_purchase_confirmed(_context("dale"))
    assert not await stage._is_purchase_confirmed(_context("ok", state=ConversationState.ORDER_COMPLETED))


def test_money_uses_the_business_currency():
    assert money(8, "BOB") == "Bs 8.00"
    assert money("514.85", None) == "Bs 514.85"
    assert money(10, "usd") == "US$ 10.00"
    assert money(5, "PEN") == "S/ 5.00"
    assert money(5, "EUR") == "EUR 5.00"
    assert money("N/A", "BOB") == "N/A"


def test_prompt_prices_in_bolivianos():
    context = _context("¿a cuánto?")
    context.relevant_products = [{"name": "Galletas de quinua", "price": "8.00", "quantity": 60}]
    stage = ResponseGeneratorStage()
    assert "Precio: Bs 8.00" in stage._build_user_prompt(context)
    system = stage._build_system_prompt(context)
    assert "Escribe los precios en Bs" in system and "nunca uses «$»" in system
    assert "*un asterisco*" in system


def test_formality_never_leaks_as_the_raw_none_token():
    # Most tenants never set personality.formality_level (onboarding leaves it unset);
    # the prompt used to literally say "Formalidad: None" to the model.
    context = _context("hola")
    system = ResponseGeneratorStage()._build_system_prompt(context)
    assert "Formalidad: None" not in system
    assert "CÓMO HABLAS" in system


def test_recent_openers_are_flagged_so_the_agent_does_not_repeat_itself():
    stage = ResponseGeneratorStage()
    history = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "¡Hola! Soy tu asistente de ventas, ¿en qué te ayudo?"},
        {"role": "user", "content": "¿tienen galletas?"},
        {"role": "assistant", "content": "¡Perfecto! Sí tenemos galletas de quinua disponibles ahora."},
    ]
    openers = stage._recent_openers(history)
    assert openers == [
        "¡Perfecto! Sí tenemos galletas de quinua",
        "¡Hola! Soy tu asistente de ventas,",
    ]

    context = _context("¿a cuánto están?", history=history)
    prompt = stage._build_user_prompt(context)
    assert "NO repitas estas aperturas que ya usaste" in prompt
    assert "¡Perfecto! Sí tenemos galletas de quinua" in prompt


def test_no_openers_warning_without_prior_agent_turns():
    stage = ResponseGeneratorStage()
    assert stage._recent_openers([]) == []
    assert stage._recent_openers([{"role": "user", "content": "hola"}]) == []
    prompt = stage._build_user_prompt(_context("hola", history=[]))
    assert "NO repitas estas aperturas" not in prompt


def test_state_instructions_reflect_the_action_this_turn_took_not_the_one_before_it():
    # Bug repro: checkout data completes and action_executor moves the state on to
    # AWAITING_PAYMENT_METHOD in this same turn. The system prompt must describe *that*,
    # not "still waiting for Nombre completo" (which context_builder used to compute from the
    # state as it was *before* action_executor ran, i.e. one turn stale).
    context = _context("Juan Perez", state=ConversationState.AWAITING_PAYMENT_METHOD)
    context.checkout_data = {"Nombre completo": "Juan Perez", "Entrega (retiro en tienda o envío)": "Retiro en tienda"}
    context.cart_contents = {"items": [{"id": 1, "name": "Chompa de lana", "price": "220.00", "quantity": 1}]}
    system = ResponseGeneratorStage()._build_system_prompt(context)
    assert "elegir su método de pago" in system
    assert "Datos que aún faltan" not in system


def test_state_instructions_still_list_missing_fields_mid_checkout():
    context = _context("hola", state=ConversationState.FULFILLMENT_COORD)
    context.checkout_data = {"Nombre completo": "Juan Perez"}
    context.cart_contents = {"items": [{"id": 1, "name": "Chompa de lana", "price": "220.00", "quantity": 1}]}
    system = ResponseGeneratorStage()._build_system_prompt(context)
    assert "Datos que aún faltan" in system
    assert "Entrega" in system


def test_the_order_is_shown_before_asking_the_data():
    context = _context("Quiero 2 galletas")
    context.cart_contents = {"items": [{"id": 1, "name": "Galletas de quinua", "price": "8.00", "quantity": 2}]}
    context.action_type, context.action_result = "collect_checkout_data", {"action": "collect_checkout_data"}
    prompt = ResponseGeneratorStage()._build_user_prompt(context)
    assert "2 x Galletas de quinua — Bs 16.00" in prompt and "Total: Bs 16.00" in prompt
    assert "muestra el pedido (CARRITO ACTUAL) con su total" in prompt


def test_unconfirmed_order_is_not_called_confirmed():
    context = _context("Quiero 2")
    context.action_type, context.action_result = "purchase_intent", {"action": "qualify_lead"}
    assert "TODAVÍA NO ESTÁ CONFIRMADO" in ResponseGeneratorStage()._build_user_prompt(context)


async def test_context_builder_reads_the_currency():
    from types import SimpleNamespace

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class Db:
        def __init__(self):
            self.values = [SimpleNamespace(tenant_id=3, configuration={}), None, None]

        async def execute(self, stmt):
            return Result(self.values.pop(0))

    config = await ContextBuilderStage()._load_agent_configuration(Db(), 21)
    assert config["tenant_info"]["currency"] == "BOB"


def test_deep_merge_does_not_let_an_unset_field_clobber_the_default():
    # personality.formality_level defaults to None in the DB schema when onboarding never
    # touched it - that used to overwrite the "casual" default with None.
    base = {"personality": {"formality_level": "casual", "tone": "friendly"}}
    override = {"personality": {"formality_level": None, "tone": "formal"}}
    ContextBuilderStage()._deep_merge(base, override)
    assert base["personality"]["formality_level"] == "casual"
    assert base["personality"]["tone"] == "formal"  # a real override still wins


def test_deep_merge_still_sets_none_for_keys_with_no_prior_default():
    base = {"integrations": {}}
    override = {"integrations": {"whatsapp_number": None}}
    ContextBuilderStage()._deep_merge(base, override)
    assert base["integrations"]["whatsapp_number"] is None


@pytest.mark.parametrize("text, expected", [
    ("Están a **Bs 8.00** 😊", "Están a *Bs 8.00* 😊"),
    ("__Total__: Bs 16", "*Total*: Bs 16"),
    ("## Tu pedido\n- 2 galletas", "*Tu pedido*\n- 2 galletas"),
    ("* Nombre\n* NIT", "- Nombre\n- NIT"),
    ("~~Bs 10~~ Bs 8", "~Bs 10~ Bs 8"),
    ("Mira [nuestro catálogo](https://x.bo/c)", "Mira nuestro catálogo: https://x.bo/c"),
    ("Ya es *negrita* de WhatsApp y _cursiva_", "Ya es *negrita* de WhatsApp y _cursiva_"),
    ("***Oferta***", "*Oferta*"),
])
def test_whatsapp_format(text, expected):
    assert to_whatsapp(text) == expected


async def test_a_confirmation_without_a_product_does_not_open_the_checkout(monkeypatch):
    from app.services.pipeline.stages.action_executor import ActionExecutorStage

    stage = ActionExecutorStage()

    async def nothing_found(*args, **kwargs):
        return {"operations": [], "cancel": False}

    monkeypatch.setattr(stage, "_extract_cart_operations", nothing_found)
    context = _context("Sí")
    context.relevant_products = [{"id": 1, "name": "Polera", "price": "50"}, {"id": 2, "name": "Galletas", "price": "8"}]
    await stage._handle_purchase_intent(context)
    assert context.action_result["action"] == "qualify_lead" and context.action_result["missing"] == "product"
    assert context.current_state == ConversationState.BROWSING and not context.cart_contents.get("items")

