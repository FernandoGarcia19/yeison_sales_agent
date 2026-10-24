"""Approving an order in Telegram records the sale (Y-12).

Needs a PostgreSQL with the yeison_migrations schema in TEST_DATABASE_URL
(see yeison_panel_backend/tests/conftest.py); skipped otherwise.
"""

import os
import uuid
from decimal import Decimal

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL or "postgresql+asyncpg://unused/unused")
if not TEST_DATABASE_URL:
    pytest.skip("TEST_DATABASE_URL is not set", allow_module_level=True)

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.api.v1 import telegram_webhook as tw  # noqa: E402
from app.models.sale import Sale  # noqa: E402
from app.services.sale_recorder import build_sale_items  # noqa: E402


@pytest.fixture
async def factory(monkeypatch):
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    sent = []

    async def fake_whatsapp(to, body, from_number):
        sent.append(body)

    async def fake_edit(chat_id, message_id, new_text):
        return None

    monkeypatch.setattr(tw, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(tw, "send_whatsapp_message", fake_whatsapp)
    monkeypatch.setattr(tw, "edit_telegram_message_text", fake_edit)
    stock_exits = []

    async def fake_discount(tenant_id, sale_id, sale_items):
        stock_exits.append((tenant_id, sale_id, [(i["product_id"], i["quantity"]) for i in sale_items]))

    monkeypatch.setattr(tw, "_discount_stock", fake_discount)
    session_factory.sent = sent
    session_factory.stock_exits = stock_exits
    yield session_factory
    await engine.dispose()


async def _order(factory, payment_method="qr", with_lead=True, items=None, currency="BOB", cart_extra=None):
    """Insert tenant, agent, conversation waiting for approval, and its lead."""
    suffix = uuid.uuid4().hex[:8]
    if items is None:
        items = [{"id": 1, "name": "Zapatilla", "price": 249.9, "quantity": 2},
                 {"id": 2, "product_name": "Medias", "price": "15.05", "quantity": 1}]
    async with factory() as db:
        tenant_id = (await db.execute(text(
            "insert into tenant (name, email, base_currency, active, created_at, last_update) "
            "values (:n, :e, :c, true, now(), now()) returning id"),
            {"n": f"T {suffix}", "e": f"{suffix}@example.com", "c": currency})).scalar_one()
        agent_id = (await db.execute(text(
            "insert into agent_instance (tenant_id, phone_number, agent_type, configuration, active, "
            "created_at, last_update) values (:t, :p, 'Ventas', '{}', true, now(), now()) returning id"),
            {"t": tenant_id, "p": f"+5917{int(suffix, 16) % 10**7:07d}"})).scalar_one()
        import json
        conv_id = (await db.execute(text(
            "insert into sales_conversation (agent_instance_id, external_user_id, messages, active, created_at, "
            "last_update, cart_contents, current_state) values (:a, '+59170000001', '[]', true, now(), now(), "
            "cast(:cart as jsonb), 'awaiting_receipt') returning id"),
            {"a": agent_id, "cart": json.dumps({"items": items, "payment_method": payment_method,
                                                "payment_proof_submitted": True, **(cart_extra or {})})})).scalar_one()
        lead_id = None
        if with_lead:
            lead_id = (await db.execute(text(
                "insert into lead (tenant_id, agent_instance_id, phone, status, active, created_at, "
                "conversation_id) values (:t, :a, '+59170000001', 'converted', true, now(), :c) returning id"),
                {"t": tenant_id, "a": agent_id, "c": conv_id})).scalar_one()
        await db.commit()
    return {"tenant_id": tenant_id, "agent_id": agent_id, "conversation_id": conv_id, "lead_id": lead_id}


async def _sales(factory, conversation_id):
    async with factory() as db:
        return list((await db.execute(
            select(Sale).where(Sale.sales_conversation_id == conversation_id))).scalars())


def test_build_sale_items_uses_exact_decimals():
    lines = build_sale_items([{"id": 7, "name": "A", "price": 0.1, "quantity": 3},
                              {"id": 8, "name": "B", "price": "2", "quantity": 0}])
    assert lines == [{"product_id": 7, "name": "A", "quantity": 3, "unit_price": "0.10", "line_total": "0.30"}]


async def test_qr_approval_records_a_paid_sale(factory):
    order = await _order(factory, payment_method="qr")
    await tw.process_telegram_callback(f"approve_sale_{order['conversation_id']}", chat_id=1, message_id=1)

    [sale] = await _sales(factory, order["conversation_id"])
    assert sale.tenant_id == order["tenant_id"] and sale.lead_id == order["lead_id"]
    assert sale.total_amount == Decimal("514.85") and sale.subtotal == Decimal("514.85")
    assert sale.payment_method == "qr" and sale.payment_status == "paid" and sale.status == "completed"
    assert sale.currency == "BOB"
    assert [i["name"] for i in sale.sale_items] == ["Zapatilla", "Medias"]
    assert factory.sent and "aprobado" in factory.sent[0]
    assert factory.stock_exits == [(order["tenant_id"], sale.id, [(1, 2), (2, 1)])]


async def test_physical_approval_records_a_pending_payment(factory):
    order = await _order(factory, payment_method="physical")
    await tw.process_telegram_callback(f"approve_sale_{order['conversation_id']}", chat_id=1, message_id=1)
    [sale] = await _sales(factory, order["conversation_id"])
    assert sale.payment_method == "physical" and sale.payment_status == "pending"


async def test_approving_twice_records_one_sale(factory):
    order = await _order(factory)
    for _ in range(2):
        await tw.process_telegram_callback(f"approve_sale_{order['conversation_id']}", chat_id=1, message_id=1)
    assert len(await _sales(factory, order["conversation_id"])) == 1
    assert len(factory.stock_exits) == 1


async def test_missing_lead_still_approves_the_order(factory):
    order = await _order(factory, with_lead=False)
    await tw.process_telegram_callback(f"approve_sale_{order['conversation_id']}", chat_id=1, message_id=1)
    assert await _sales(factory, order["conversation_id"]) == []
    assert factory.stock_exits == []
    async with factory() as db:
        state = (await db.execute(text("select current_state from sales_conversation where id = :i"),
                                  {"i": order["conversation_id"]})).scalar_one()
    assert state == "order_completed"
    assert factory.sent  # the customer was still notified


async def test_rejection_records_nothing(factory):
    order = await _order(factory)
    await tw.process_telegram_callback(f"reject_sale_{order['conversation_id']}", chat_id=1, message_id=1)
    assert await _sales(factory, order["conversation_id"]) == []


async def test_a_sale_keeps_the_invoice_the_customer_asked_for(factory):
    order = await _order(factory, cart_extra={"invoice_requested": True, "checkout_data": {
        "Nombre completo": "Carla M.", "NIT o CI para la factura": "4567890", "Razón social para la factura": "Carla SRL"}})
    await tw.process_telegram_callback(f"approve_sale_{order['conversation_id']}", chat_id=1, message_id=1)
    [sale] = await _sales(factory, order["conversation_id"])
    assert sale.invoice_request["tax_id"] == "4567890" and sale.invoice_request["name"] == "Carla SRL"
    assert sale.invoice_request["requested_at"]


async def test_a_sale_without_the_request_has_none(factory):
    order = await _order(factory, cart_extra={"checkout_data": {"Nombre completo": "Carla M.", "NIT": "99"}})
    await tw.process_telegram_callback(f"approve_sale_{order['conversation_id']}", chat_id=1, message_id=1)
    [sale] = await _sales(factory, order["conversation_id"])
    assert sale.invoice_request is None
