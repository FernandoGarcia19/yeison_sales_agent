"""The panel's inbox (Y-69): pause, answer as a person and resume a WhatsApp conversation.

The key checks need nothing; the rest needs a PostgreSQL with the yeison_migrations schema in
TEST_DATABASE_URL. WhatsApp and Redis are faked.
"""

import json
import os
import uuid

import httpx
import pytest
from fastapi import FastAPI

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL or "postgresql+asyncpg://unused/unused")

from app.api.v1 import internal_conversations  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services import inbox  # noqa: E402

needs_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")
KEY = "inbox-test-key"


async def _post(path, body, headers=None):
    app = FastAPI()
    app.include_router(internal_conversations.router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(f"/api/v1/internal/conversations{path}", json=body, headers=headers or {})


@pytest.mark.parametrize("action", ["pause", "resume", "reply"])
async def test_the_routes_refuse_without_the_internal_key(monkeypatch, action):
    monkeypatch.setattr(settings, "backend_api_key", KEY)
    body = {"tenant_id": 1, "text": "hola"}
    assert (await _post(f"/1/{action}", body)).status_code == 401
    assert (await _post(f"/1/{action}", body, {"x-internal-key": "otra"})).status_code == 401


async def test_without_a_configured_key_everything_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "backend_api_key", "")
    assert (await _post("/1/pause", {"tenant_id": 1}, {"x-internal-key": ""})).status_code == 503


@pytest.fixture
async def business(monkeypatch):
    """Two tenants, each with an agent; the first has a conversation in the middle of an order."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core import database

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    monkeypatch.setattr(database, "_engine", engine)
    monkeypatch.setattr(database, "_session_factory", None)
    monkeypatch.setattr(settings, "backend_api_key", KEY)
    suffix = uuid.uuid4().hex[:8]
    ids = {}
    async with engine.begin() as conn:
        for i, name in enumerate(("a", "b")):
            tenant_id = (await conn.execute(text(
                "insert into tenant (name, email, base_currency, active, created_at, last_update) "
                "values (:n, :e, 'BOB', true, now(), now()) returning id"),
                {"n": f"Inbox {name} {suffix}", "e": f"inbox{name}{suffix}@example.com"})).scalar_one()
            config = {"conversation_settings": {"pause_hours": 2}}
            agent_id = (await conn.execute(text(
                "insert into agent_instance (tenant_id, phone_number, agent_type, configuration, active, created_at, "
                "last_update) values (:t, :p, 'Ventas', cast(:c as jsonb), true, now(), now()) returning id"),
                {"t": tenant_id, "p": f"+5917{(int(suffix, 16) + i) % 10**7:07d}",
                 "c": json.dumps(config)})).scalar_one()
            ids[name] = (tenant_id, agent_id)
        conversation_id = (await conn.execute(text(
            "insert into sales_conversation (agent_instance_id, external_user_id, messages, current_state, "
            "cart_contents, active, created_at, last_update) values (:a, '+59171234567', cast(:m as jsonb), "
            "'fulfillment_coord', cast(:c as jsonb), true, now(), now()) returning id"),
            {"a": ids["a"][1], "m": json.dumps([{"role": "user", "content": "quiero 2 poleras"}]),
             "c": json.dumps({"items": [{"id": 1, "name": "Polera", "price": 80, "quantity": 2}]})})).scalar_one()
    sent = []

    async def fake_send(to, body, from_number, media_url=None):
        sent.append({"to": to, "body": body, "from": from_number})
        return f"sid-{len(sent)}"

    async def fake_delete(key):
        return True

    monkeypatch.setattr(inbox, "send_whatsapp_message", fake_send)
    monkeypatch.setattr("app.core.redis_client.cache_delete", fake_delete)
    yield {"a": ids["a"][0], "b": ids["b"][0], "conversation_id": conversation_id, "engine": engine, "sent": sent}
    await engine.dispose()


async def _stored(engine, conversation_id):
    from sqlalchemy import text

    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "select current_state, cart_contents, messages from sales_conversation where id = :i"),
            {"i": conversation_id})).one()
    return row[0], row[1] or {}, row[2] or []


@needs_db
async def test_a_reply_goes_out_is_kept_as_a_person_and_pauses_the_agent(business):
    conversation_id = business["conversation_id"]
    result = await inbox.reply(conversation_id, business["a"], "Hola, soy **Ana**: te lo dejo en Bs 150", "Ana")

    assert business["sent"] == [{"to": "+59171234567", "body": "Hola, soy *Ana*: te lo dejo en Bs 150",
                                 "from": business["sent"][0]["from"]}]
    state, cart, messages = await _stored(business["engine"], conversation_id)
    assert state == "paused" and result["state"] == "paused"
    assert cart["state_before_pause"] == "fulfillment_coord" and cart["paused_until"]
    assert cart["items"][0]["name"] == "Polera"
    last = messages[-1]
    assert last["role"] == "assistant" and last["sender"] == "human" and last["author"] == "Ana"
    assert last["content"] == "Hola, soy *Ana*: te lo dejo en Bs 150" and last["message_sid"] == "sid-1"

    # a second reply keeps the state to go back to
    await inbox.reply(conversation_id, business["a"], "¿Te lo envío hoy?")
    _, cart, messages = await _stored(business["engine"], conversation_id)
    assert cart["state_before_pause"] == "fulfillment_coord" and len(messages) == 3


@needs_db
async def test_resume_gives_back_the_state_before_the_pause(business):
    conversation_id = business["conversation_id"]
    paused = await inbox.pause_conversation(conversation_id, business["a"])
    assert paused["state"] == "paused" and paused["state_before_pause"] == "fulfillment_coord"

    resumed = await inbox.resume_conversation(conversation_id, business["a"])
    state, cart, _ = await _stored(business["engine"], conversation_id)
    assert resumed["state"] == state == "fulfillment_coord"
    assert "paused_until" not in cart and "state_before_pause" not in cart and cart["items"]

    # resuming a conversation that is not paused changes nothing
    assert (await inbox.resume_conversation(conversation_id, business["a"]))["state"] == "fulfillment_coord"


@needs_db
async def test_another_business_cannot_touch_the_conversation(business):
    conversation_id = business["conversation_id"]
    headers = {"x-internal-key": KEY}
    for action, body in (("pause", {}), ("resume", {}), ("reply", {"text": "hola"})):
        response = await _post(f"/{conversation_id}/{action}", {"tenant_id": business["b"], **body}, headers)
        assert response.status_code == 404
    state, _, messages = await _stored(business["engine"], conversation_id)
    assert state == "fulfillment_coord" and len(messages) == 1 and business["sent"] == []

    ok = await _post(f"/{conversation_id}/pause", {"tenant_id": business["a"]}, headers)
    assert ok.status_code == 200 and ok.json()["state"] == "paused"


@needs_db
async def test_a_failed_send_changes_nothing(business, monkeypatch):
    async def broken(**kwargs):
        raise RuntimeError("evolution down")

    monkeypatch.setattr(inbox, "send_whatsapp_message", broken)
    with pytest.raises(inbox.InboxError) as error:
        await inbox.reply(business["conversation_id"], business["a"], "hola")
    assert error.value.status == 502
    state, _, messages = await _stored(business["engine"], business["conversation_id"])
    assert state == "fulfillment_coord" and len(messages) == 1
