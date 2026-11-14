"""Agent test chat: the real flows run, nothing persists and nothing leaves the service.

Needs a PostgreSQL with the yeison_migrations schema in TEST_DATABASE_URL. Redis and the LLM
are faked; the customer's turn uses a stand-in pipeline that writes like the real one.
"""

import json
import os
import uuid

import httpx
import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL or "postgresql+asyncpg://unused/unused")

from app.core import simulation  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services import simulator  # noqa: E402

needs_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")
ITEMS = [{"id": 1, "name": "Polera básica", "price": 80, "quantity": 2}]


def test_available_actions_follow_the_payment_step():
    assert simulator.available_actions({"current_state": "browsing"}) == []
    waiting = {"current_state": "awaiting_receipt"}
    assert simulator.available_actions({**waiting, "cart_contents": {"payment_method": "qr"}}) == ["receipt"]
    assert simulator.available_actions({**waiting, "cart_contents": {"payment_proof_submitted": True}}) == \
        ["approve", "reject"]
    assert simulator.available_actions({**waiting, "cart_contents": {"payment_method": "physical"}}) == \
        ["approve", "reject"]
    assert simulator.available_actions({**waiting, "cart_contents": {"bank_qr": {"status": "pending"}}}) == \
        ["pay", "receipt"]


def test_a_paused_conversation_can_be_resumed():
    # Y-58/Y-60: the supervisor's «Reanudar agente» button
    assert simulator.available_actions({"current_state": "paused"}) == ["resume"]


def test_understood_says_what_the_agent_made_of_the_message():
    from types import SimpleNamespace

    context = SimpleNamespace(
        action_result={"action": "cart_updated", "cart_changes": ["Se agregó 1 x Gorra."], "missing_fields": ["Dirección de entrega"]},
        cart_contents={"delivery_method": "delivery", "invoice_requested": True},
    )
    assert simulator.understood(context) == {
        "cart_changes": ["Se agregó 1 x Gorra."], "missing_fields": ["Dirección de entrega"], "delivery_method": "delivery",
        "invoice_requested": True, "previous_order_discarded": False, "handoff": False, "missing_product": False,
    }
    empty = simulator.understood(SimpleNamespace(action_result=None, cart_contents=None))
    assert empty["cart_changes"] == [] and empty["delivery_method"] is None


def test_capture_only_inside_a_run():
    with pytest.raises(RuntimeError):
        simulation.capture("whatsapp", body="x")


def test_customer_phone_fits_the_columns():
    assert len(simulator.customer_phone("a" * 32)) <= 20


@pytest.fixture
def redis(monkeypatch):
    store, locks = {}, set()

    async def get(key):
        return json.loads(store[key]) if key in store else None

    async def set_(key, value, ttl=None):
        store[key] = json.dumps(value)
        return True

    async def delete(key):
        return store.pop(key, None) is not None

    async def lock(key, ttl=10):
        if key in locks:
            return False
        locks.add(key)
        return True

    async def unlock(key):
        locks.discard(key)
        return True

    for name, fn in (("cache_get", get), ("cache_set", set_), ("cache_delete", delete),
                     ("acquire_lock", lock), ("release_lock", unlock)):
        monkeypatch.setattr(simulator, name, fn)
    return store


@pytest.fixture
async def business(monkeypatch):
    """A tenant with an agent; the engine points at the test database."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core import database

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    monkeypatch.setattr(database, "_engine", engine)
    monkeypatch.setattr(database, "_session_factory", None)
    monkeypatch.setattr(settings, "telegram_bot_token", "t")
    suffix = uuid.uuid4().hex[:8]
    async with engine.begin() as conn:
        tenant_id = (await conn.execute(text(
            "insert into tenant (name, email, base_currency, active, created_at, last_update) "
            "values (:n, :e, 'BOB', true, now(), now()) returning id"),
            {"n": f"Sim {suffix}", "e": f"sim{suffix}@example.com"})).scalar_one()
        config = {"integrations": {"telegram": {"enabled": True, "supervisor_chat_id": "1"}},
                  "sales_process": {"bank_QR": True, "physical_payment": True}}
        agent_id = (await conn.execute(text(
            "insert into agent_instance (tenant_id, phone_number, agent_type, configuration, active, created_at, "
            "last_update) values (:t, :p, 'Ventas', cast(:c as jsonb), true, now(), now()) returning id"),
            {"t": tenant_id, "p": f"+5916{int(suffix, 16) % 10**7:07d}", "c": json.dumps(config)})).scalar_one()
    yield {"tenant_id": tenant_id, "agent_id": agent_id, "engine": engine}
    await engine.dispose()


async def _counts(engine, tenant_id):
    from sqlalchemy import text

    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "select (select count(*) from lead where tenant_id = :t), "
            "(select count(*) from sale where tenant_id = :t), "
            "(select count(*) from sales_conversation c join agent_instance a on a.id = c.agent_instance_id "
            " where a.tenant_id = :t)"), {"t": tenant_id})).one()
    return tuple(row)


@needs_db
async def test_a_customer_turn_writes_nothing_and_sends_nothing(business, redis, monkeypatch):
    """The stand-in pipeline writes through the same factory as the real stages and sends a WhatsApp."""
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from app.core.database import get_session_factory
    from app.integrations.whatsapp.client import send_whatsapp_message
    from app.models import SalesConversation
    from app.services.pipeline import runner as runner_module

    sent_for_real = []
    monkeypatch.setattr("app.integrations.whatsapp.client.send_evolution_message",
                        lambda **kw: sent_for_real.append(kw))

    class FakeResult:
        intent, action_executed, error, processing_time_ms = "purchase_intent", "collect_checkout_data", None, 12.0

    async def fake_run(self, context):
        from app.services.pipeline.stages.identifier import IdentificationStage

        context = await IdentificationStage().process(context)
        async with get_session_factory()() as db:
            conv = (await db.execute(select(SalesConversation).where(
                SalesConversation.id == context.conversation_id))).scalar_one()
            conv.add_message(role="user", content=context.message_body)
            conv.add_message(role="assistant", content="¿A qué dirección te lo enviamos?")
            conv.cart_contents = {"items": ITEMS}
            conv.current_state = "fulfillment_coord"
            flag_modified(conv, "messages")  # as response_generator does
            await db.commit()
        await send_whatsapp_message(to=context.sender_phone, body="¿A qué dirección te lo enviamos?",
                                    from_number=context.recipient_phone)
        return FakeResult()

    monkeypatch.setattr(runner_module.PipelineRunner, "run", fake_run)
    before = await _counts(business["engine"], business["tenant_id"])

    view = await simulator.run_turn(business["tenant_id"], business["agent_id"], "abc12345", "message",
                                    "quiero 2 poleras")

    assert await _counts(business["engine"], business["tenant_id"]) == before == (0, 0, 0)
    assert sent_for_real == []
    assert [e["from"] for e in view["transcript"]] == ["customer", "agent"]
    assert view["transcript"][1]["text"] == "¿A qué dirección te lo enviamos?"
    assert view["state"] == "fulfillment_coord" and view["cart"][0]["name"] == "Polera básica"
    assert view["trace"]["intent"] == "purchase_intent"

    # the next turn starts from where this one left off
    stored = json.loads(redis[simulator._key(business["tenant_id"], business["agent_id"], "abc12345")])
    assert [m["role"] for m in stored["messages"]] == ["user", "assistant"]


@needs_db
async def test_supervisor_approval_is_simulated(business, redis):
    key = simulator._key(business["tenant_id"], business["agent_id"], "beef0001")
    state = simulator._empty_state()
    state.update(current_state="awaiting_receipt",
                 cart_contents={"items": ITEMS, "payment_method": "physical", "payment_proof_submitted": True})
    redis[key] = json.dumps(state)

    view = await simulator.run_turn(business["tenant_id"], business["agent_id"], "beef0001", "approve")

    assert await _counts(business["engine"], business["tenant_id"]) == (0, 0, 0)
    assert view["state"] == "order_completed" and view["actions"] == []
    texts = [e["text"] for e in view["events"]]
    assert any("confirmada" in t for t in texts)  # the customer's WhatsApp
    assert any("Se descontaría del stock: Polera básica x2" in t for t in texts)


@needs_db
async def test_resume_is_simulated(business, redis):
    key = simulator._key(business["tenant_id"], business["agent_id"], "beef0002")
    state = simulator._empty_state()
    state.update(current_state="paused", cart_contents={"items": ITEMS, "state_before_pause": "cart_building",
                                                        "paused_until": "2999-01-01T00:00:00"})
    redis[key] = json.dumps(state)

    view = await simulator.run_turn(business["tenant_id"], business["agent_id"], "beef0002", "resume")

    assert view["state"] == "cart_building" and view["actions"] == []
    assert view["transcript"][-1]["text"].startswith("👤 El supervisor reanudó")
    assert await _counts(business["engine"], business["tenant_id"]) == (0, 0, 0)


@needs_db
async def test_bank_payment_is_simulated_without_the_bank(business, redis, monkeypatch):
    monkeypatch.setattr(settings, "bank_qr_api_url", "http://qr:3000")
    monkeypatch.setattr(settings, "bank_qr_api_key", "k" * 40)

    async def no_network(*a, **k):
        raise AssertionError("the test chat must not call the QR service")

    monkeypatch.setattr(httpx.AsyncClient, "request", no_network)
    key = simulator._key(business["tenant_id"], business["agent_id"], "cafe0001")
    state = simulator._empty_state()
    state.update(current_state="awaiting_receipt",
                 cart_contents={"items": ITEMS, "payment_method": "qr",
                                "bank_qr": {"alias": "sim-1", "amount": "160.00", "currency": "BOB",
                                            "status": "pending"}},
                 bank_charges={"sim-1": {"alias": "sim-1", "tenantId": str(business["tenant_id"]),
                                         "estado": "PENDIENTE", "monto": 160}})
    redis[key] = json.dumps(state)
    assert simulator.available_actions(state) == ["pay", "receipt"]

    view = await simulator.run_turn(business["tenant_id"], business["agent_id"], "cafe0001", "pay")

    assert view["trace"]["outcome"] == "recorded"
    assert view["state"] == "order_completed" and view["payment"]["bank_qr"]["status"] == "paid"
    assert await _counts(business["engine"], business["tenant_id"]) == (0, 0, 0)
    froms = [e["from"] for e in view["events"]]
    assert "agent" in froms and "supervisor_telegram" in froms
    assert any("Bs 160.00" in e["text"] for e in view["events"] if e["from"] == "agent")


@needs_db
async def test_actions_that_do_not_apply_are_refused(business, redis):
    with pytest.raises(simulator.SimulatorError) as e:
        await simulator.run_turn(business["tenant_id"], business["agent_id"], "dead0001", "pay")
    assert e.value.status == 409


@needs_db
async def test_another_business_agent_is_not_found(business, redis):
    with pytest.raises(simulator.SimulatorError) as e:
        await simulator.run_turn(business["tenant_id"] + 999999, business["agent_id"], "dead0002", "message", "hola")
    assert e.value.status == 404


async def test_endpoint_needs_the_internal_key(monkeypatch):
    from main import app

    monkeypatch.setattr(settings, "backend_api_key", "i" * 40)
    body = {"tenant_id": 1, "agent_id": 1, "session_id": "abc12345", "action": "reset"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        assert (await client.post("/api/v1/internal/simulator/turn", json=body,
                                  headers={"x-internal-key": "wrong"})).status_code == 401
        bad = {**body, "session_id": "NOT-HEX!"}
        assert (await client.post("/api/v1/internal/simulator/turn", json=bad,
                                  headers={"x-internal-key": "i" * 40})).status_code == 422
