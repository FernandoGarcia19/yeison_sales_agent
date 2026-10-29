"""Bank QR through SIP (BMSCYeisonQRGenerator): charge, payment notice, poller and fallbacks.

The QR service is faked; the database parts need a PostgreSQL with the yeison_migrations
schema in TEST_DATABASE_URL (see yeison_panel_backend/tests/conftest.py).
"""

import json
import os
import uuid
from decimal import Decimal

import httpx
import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL or "postgresql+asyncpg://unused/unused")

from app.core.config import settings  # noqa: E402
from app.services import bank_qr  # noqa: E402

needs_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")

ITEMS = [{"id": 1, "name": "Zapatilla", "price": 249.9, "quantity": 2},
         {"id": 2, "product_name": "Medias", "price": "15.05", "quantity": 1}]


class FakeQrService:
    """In-memory stand-in for the QR service's internal API."""

    def __init__(self):
        self.charges = {}
        self.calls = []
        self.fail = False

    async def call(self, method, path, json=None):
        self.calls.append((method, path, json))
        if self.fail:
            raise bank_qr.BankQrError("QR service unreachable")
        if method == "POST" and path == "/sip/qr":
            alias = uuid.uuid4().hex
            self.charges[alias] = {"alias": alias, "tenantId": json["tenantId"], "estado": "PENDIENTE",
                                   "monto": json["monto"]}
            return {"alias": alias, "estado": "PENDIENTE", "idQr": "QR1", "fechaVencimiento": "30/10/2026",
                    "imagenQr": "iVBORw0KGgo="}
        alias = path.split("/")[3]
        if alias not in self.charges:
            # the real service answers 404 (e.g. charges left by other tests in the same database)
            raise bank_qr.BankQrError("QR service answered 404")
        if path.endswith("/inhabilitar"):
            self.charges[alias]["estado"] = "INHABILITADO"
        return dict(self.charges[alias])

    def pay(self, alias):
        self.charges[alias]["estado"] = "PAGADO"


@pytest.fixture
def qr(monkeypatch):
    fake = FakeQrService()
    monkeypatch.setattr(bank_qr, "_call", fake.call)
    monkeypatch.setattr(settings, "bank_qr_api_url", "http://qr:3000")
    monkeypatch.setattr(settings, "bank_qr_api_key", "k" * 40)
    return fake


# --- pure helpers ---------------------------------------------------------------------------

def test_cart_total_uses_the_stored_prices():
    assert bank_qr.cart_total(ITEMS) == Decimal("514.85")


def test_glosa_fits_sip_limit():
    assert bank_qr.glosa(123456789012, 987654321098) == "Pedido 123456789012-98765432109"[:30]
    assert len(bank_qr.glosa(10**15, 10**15)) <= 30


def test_enabled_needs_the_switch_and_the_service(monkeypatch):
    monkeypatch.setattr(settings, "bank_qr_api_url", None)
    monkeypatch.setattr(settings, "bank_qr_api_key", None)
    assert not bank_qr.enabled_for({"bank_QR": True})
    monkeypatch.setattr(settings, "bank_qr_api_url", "http://qr:3000")
    monkeypatch.setattr(settings, "bank_qr_api_key", "k" * 40)
    assert bank_qr.enabled_for({"bank_QR": True})
    assert not bank_qr.enabled_for({"QR_payment": True})
    assert not bank_qr.enabled_for(None)


def test_pending_charge():
    assert bank_qr.pending_charge({"bank_qr": {"alias": "a", "status": "pending"}})["alias"] == "a"
    assert bank_qr.pending_charge({"bank_qr": {"alias": "a", "status": "paid"}}) is None
    assert bank_qr.pending_charge(None) is None


# --- notice endpoint ------------------------------------------------------------------------

async def _post_notice(monkeypatch, key, notify_key, scheduled):
    from main import app

    monkeypatch.setattr(settings, "bank_qr_notify_key", notify_key)

    async def fake_confirm(alias, source, charge=None):
        scheduled.append((alias, source))
        return "recorded"

    monkeypatch.setattr(bank_qr, "confirm_payment", fake_confirm)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        return await client.post("/api/v1/internal/payments/bank-qr", json={"alias": "abc"},
                                 headers={"x-api-key": key})


async def test_notice_needs_the_key(monkeypatch):
    scheduled = []
    assert (await _post_notice(monkeypatch, "wrong", "n" * 40, scheduled)).status_code == 401
    assert (await _post_notice(monkeypatch, "n" * 40, None, scheduled)).status_code == 503
    assert scheduled == []


async def test_notice_schedules_the_confirmation(monkeypatch):
    scheduled = []
    response = await _post_notice(monkeypatch, "n" * 40, "n" * 40, scheduled)
    assert response.status_code == 200
    assert scheduled == [("abc", "notice")]


# --- confirmation against the database ------------------------------------------------------

@pytest.fixture
async def db(monkeypatch, qr):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.api.v1 import telegram_webhook as tw
    from app.integrations.whatsapp import client as wa_client
    from app.services import telegram_service

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(bank_qr, "get_session_factory", lambda: factory)
    factory.sent, factory.stock_exits, factory.telegram = [], [], []

    async def fake_whatsapp(to, body, from_number, media_url=None):
        factory.sent.append({"to": to, "body": body, "media_url": media_url})

    async def fake_discount(tenant_id, sale_id, sale_items):
        factory.stock_exits.append(sale_id)

    async def fake_telegram(chat_id, sale_data):
        factory.telegram.append(sale_data)

    monkeypatch.setattr(wa_client, "send_whatsapp_message", fake_whatsapp)
    monkeypatch.setattr(tw, "_discount_stock", fake_discount)
    monkeypatch.setattr(telegram_service, "send_bank_qr_paid_notification", fake_telegram)
    monkeypatch.setattr(settings, "telegram_bot_token", "t")
    yield factory
    await engine.dispose()


async def _waiting_order(db, qr, state="awaiting_receipt", tenant_for_charge=None):
    """A conversation waiting on a pending bank QR, with its lead and the charge in the fake service."""
    from sqlalchemy import text

    suffix = uuid.uuid4().hex[:8]
    async with db() as s:
        tenant_id = (await s.execute(text(
            "insert into tenant (name, email, base_currency, active, created_at, last_update) "
            "values (:n, :e, 'BOB', true, now(), now()) returning id"),
            {"n": f"T {suffix}", "e": f"{suffix}@example.com"})).scalar_one()
        config = {"integrations": {"telegram": {"enabled": True, "supervisor_chat_id": "99"}},
                  "sales_process": {"bank_QR": True}}
        agent_id = (await s.execute(text(
            "insert into agent_instance (tenant_id, phone_number, agent_type, configuration, active, "
            "created_at, last_update) values (:t, :p, 'Ventas', cast(:c as jsonb), true, now(), now()) returning id"),
            {"t": tenant_id, "p": f"+5917{int(suffix, 16) % 10**7:07d}", "c": json.dumps(config)})).scalar_one()
        charge = await qr.call("POST", "/sip/qr", {"tenantId": str(tenant_for_charge or tenant_id), "monto": 514.85})
        alias = charge["alias"]
        cart = {"items": ITEMS, "payment_method": "qr", "checkout_data": {"Dirección": "Calle 1"},
                "bank_qr": {"alias": alias, "amount": "514.85", "currency": "BOB", "status": "pending"}}
        conv_id = (await s.execute(text(
            "insert into sales_conversation (agent_instance_id, external_user_id, messages, active, created_at, "
            "last_update, cart_contents, current_state) values (:a, '+59170000001', '[]', true, now(), now(), "
            "cast(:cart as jsonb), :state) returning id"),
            {"a": agent_id, "cart": json.dumps(cart), "state": state})).scalar_one()
        lead_id = (await s.execute(text(
            "insert into lead (tenant_id, agent_instance_id, phone, status, active, created_at, conversation_id) "
            "values (:t, :a, '+59170000001', 'new', true, now(), :c) returning id"),
            {"t": tenant_id, "a": agent_id, "c": conv_id})).scalar_one()
        await s.commit()
    return {"alias": alias, "tenant_id": tenant_id, "conversation_id": conv_id, "lead_id": lead_id}


async def _state(db, order):
    from sqlalchemy import text

    async with db() as s:
        row = (await s.execute(text(
            "select c.current_state, c.cart_contents, l.status from sales_conversation c "
            "join lead l on l.conversation_id = c.id where c.id = :i"), {"i": order["conversation_id"]})).one()
        sales = (await s.execute(text(
            "select total_amount, payment_method, payment_status, payment_reference from sale "
            "where sales_conversation_id = :i"), {"i": order["conversation_id"]})).all()
    return row, sales


@needs_db
async def test_paid_qr_records_the_sale_once(db, qr):
    order = await _waiting_order(db, qr)
    qr.pay(order["alias"])

    assert await bank_qr.confirm_payment(order["alias"], source="notice") == "recorded"
    assert await bank_qr.confirm_payment(order["alias"], source="poll") == "already_recorded"

    (state, cart, lead_status), sales = await _state(db, order)
    assert state == "order_completed" and lead_status == "converted"
    assert cart["bank_qr"]["status"] == "paid" and cart["bank_qr"]["confirmed_by"] == "notice"
    assert sales == [(Decimal("514.85"), "qr", "paid", order["alias"])]
    assert len(db.sent) == 1 and "Bs 514.85" in db.sent[0]["body"]
    assert len(db.stock_exits) == 1
    assert db.telegram[0]["total_price"] == "Bs 514.85"


@needs_db
async def test_unpaid_qr_records_nothing(db, qr):
    order = await _waiting_order(db, qr)
    assert await bank_qr.confirm_payment(order["alias"], source="notice") == "not_paid"
    (state, _, _), sales = await _state(db, order)
    assert state == "awaiting_receipt" and sales == [] and db.sent == []


@needs_db
async def test_charge_of_another_tenant_is_refused(db, qr):
    order = await _waiting_order(db, qr, tenant_for_charge=999999999)
    qr.pay(order["alias"])
    assert await bank_qr.confirm_payment(order["alias"], source="notice") == "tenant_mismatch"
    assert (await _state(db, order))[1] == []


@needs_db
async def test_unknown_alias(db, qr):
    charge = await qr.call("POST", "/sip/qr", {"tenantId": "1", "monto": 1})
    qr.pay(charge["alias"])
    assert await bank_qr.confirm_payment(charge["alias"], source="notice") == "unknown_alias"


@needs_db
async def test_sale_approved_by_the_supervisor_gets_the_reference_quietly(db, qr):
    from sqlalchemy import text

    order = await _waiting_order(db, qr, state="order_completed")
    async with db() as s:
        await s.execute(text(
            "insert into sale (tenant_id, lead_id, sales_conversation_id, sale_date, status, subtotal, tax, "
            "discount, total_amount, payment_method, payment_status, sale_items, created_at, updated_at, active) "
            "values (:t, :l, :c, now(), 'completed', 514.85, 0, 0, 514.85, 'qr', 'paid', '[]', now(), now(), true)"),
            {"t": order["tenant_id"], "l": order["lead_id"], "c": order["conversation_id"]})
        await s.commit()
    qr.pay(order["alias"])

    assert await bank_qr.confirm_payment(order["alias"], source="notice") == "already_recorded"
    _, sales = await _state(db, order)
    assert sales == [(Decimal("514.85"), "qr", "paid", order["alias"])]
    assert db.sent == [] and db.stock_exits == []


@needs_db
async def test_poller_confirms_paid_and_leaves_pending(db, qr):
    paid = await _waiting_order(db, qr)
    waiting = await _waiting_order(db, qr)
    qr.pay(paid["alias"])

    assert await bank_qr.poll_pending_once() >= 1
    assert (await _state(db, paid))[0][0] == "order_completed"
    assert (await _state(db, waiting))[0][0] == "awaiting_receipt"


@needs_db
async def test_service_down_does_not_break_the_check(db, qr):
    order = await _waiting_order(db, qr)
    qr.fail = True
    assert await bank_qr.check_and_confirm(order["alias"], source="poll") == "unavailable"


# --- the agent's flow -----------------------------------------------------------------------

def _context(**cart):
    from app.schemas.pipeline import PipelineContext

    return PipelineContext(
        message_body="confirmo", sender_phone="+59170000001", recipient_phone="+59170000002",
        message_sid="m1", tenant_id=7, conversation_id=11, lead_id=22, agent_config={},
        cart_contents={"items": ITEMS, **cart},
    )


@pytest.fixture
def executor(monkeypatch, qr):
    from app.integrations.whatsapp import client as wa_client
    from app.services import r2_storage
    from app.services.pipeline.stages.action_executor import ActionExecutorStage

    sent, uploads = [], []

    async def fake_whatsapp(to, body, from_number, media_url=None):
        sent.append({"body": body, "media_url": media_url})

    async def fake_upload(key, data, content_type):
        uploads.append((key, content_type))
        return key

    async def fake_currency(tenant_id):
        return "BOB"

    monkeypatch.setattr(wa_client, "send_whatsapp_message", fake_whatsapp)
    monkeypatch.setattr(r2_storage, "upload_bytes", fake_upload)
    monkeypatch.setattr(r2_storage, "get_presigned_url", lambda key, expires_in=900: f"https://r2/{key}")
    monkeypatch.setattr(bank_qr, "tenant_currency", fake_currency)
    stage = ActionExecutorStage()
    stage.sent, stage.uploads = sent, uploads
    return stage


async def test_agent_sends_a_qr_for_the_exact_total(executor, qr):
    context = _context()
    assert await executor._handle_bank_qr_flow(context) is True

    charge = context.cart_contents["bank_qr"]
    assert charge["status"] == "pending" and charge["amount"] == "514.85" and charge["currency"] == "BOB"
    assert qr.calls[0] == ("POST", "/sip/qr", {"tenantId": "7", "monto": 514.85, "glosa": "Pedido 11-22",
                                               "moneda": "BOB"})
    assert executor.uploads == [(f"payments/bank_qr/tenant_7/{charge['alias']}.png", "image/png")]
    [message] = executor.sent
    assert "Bs 514.85" in message["body"] and "no hace falta que envíes el comprobante" in message["body"]
    assert message["media_url"].endswith(".png")
    assert context.current_state.value == "awaiting_receipt" and context.response_already_sent


async def test_new_qr_disables_the_previous_one(executor, qr):
    context = _context()
    await executor._handle_bank_qr_flow(context)
    first = context.cart_contents["bank_qr"]["alias"]
    await executor._handle_bank_qr_flow(context)
    assert qr.charges[first]["estado"] == "INHABILITADO"
    assert context.cart_contents["bank_qr"]["alias"] != first


async def test_service_down_falls_back_to_the_static_qr(executor, qr):
    qr.fail = True
    context = _context()
    assert await executor._handle_bank_qr_flow(context) is False
    assert "bank_qr" not in context.cart_contents and executor.sent == []


async def test_empty_cart_falls_back(executor, qr):
    context = _context()
    context.cart_contents["items"] = []
    assert await executor._handle_bank_qr_flow(context) is False
    assert qr.calls == []
