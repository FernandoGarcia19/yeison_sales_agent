"""A failed turn does not leave the customer in silence, and a dropped WhatsApp reaches the panel (Y-63)."""

import json
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused/unused")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.api.v1 import webhooks  # noqa: E402
from app.core import simulation  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.integrations.whatsapp import evolution  # noqa: E402
from app.services import batch_manager, connection_updates, telegram_service, turn_failure  # noqa: E402

AGENT, CUSTOMER = "+59160000000", "+59170000001"


class Result:
    def __init__(self, success, response_sent, error=None):
        self.success, self.response_sent, self.error = success, response_sent, error
        self.message_sid, self.intent, self.action_executed = "m1", None, None


class Context:
    recipient_phone, sender_phone, message_body, profile_name = AGENT, CUSTOMER, "hola, ¿tienen <poleras>?", "Ana"


@pytest.fixture
def world(monkeypatch):
    """The agent's settings, a throttle in memory, and everything that would be sent."""
    sent = {"whatsapp": [], "supervisor": [], "telegram": [], "claimed": set()}
    config = {"integrations": {"supervisor_number": "+59179999999",
                               "telegram": {"enabled": True, "supervisor_chat_id": "777"}}}

    async def agent_config(phone):
        return config if phone == AGENT else None

    async def claim(key, ttl=10):
        assert ttl == turn_failure.QUIET_SECONDS
        if key in sent["claimed"]:
            return False
        sent["claimed"].add(key)
        return True

    async def send_whatsapp(to, body, from_number, media_url=None):
        sent["whatsapp"].append((to, body, from_number))
        return "wamid.1"

    async def notify_supervisor(**kwargs):
        sent["supervisor"].append(kwargs)
        return True

    async def telegram(chat_id, data):
        sent["telegram"].append((chat_id, data))
        return True

    monkeypatch.setattr(turn_failure, "_agent_config", agent_config)
    monkeypatch.setattr(turn_failure, "acquire_lock", claim)
    monkeypatch.setattr(turn_failure, "send_whatsapp_message", send_whatsapp)
    monkeypatch.setattr(turn_failure.NotificationService, "notify_handoff_request", staticmethod(notify_supervisor))
    monkeypatch.setattr(telegram_service, "send_turn_failure_notification", telegram)
    monkeypatch.setattr(settings, "telegram_bot_token", "t")
    sent["config"] = config
    return sent


def test_only_a_failed_turn_without_an_answer_needs_the_fallback():
    assert turn_failure.needs_fallback(Result(success=False, response_sent=False))
    assert not turn_failure.needs_fallback(Result(success=True, response_sent=True))
    assert not turn_failure.needs_fallback(Result(success=True, response_sent=False))  # paused: silence is meant
    assert not turn_failure.needs_fallback(Result(success=False, response_sent=True))  # answered, failed after
    assert not turn_failure.needs_fallback(None)


async def test_the_customer_and_the_supervisor_are_told_once(world):
    assert await turn_failure.report(Context(), result=Result(False, False, "llm: timeout")) is True
    assert world["whatsapp"] == [(CUSTOMER, turn_failure.FALLBACK_TEXT, AGENT)]
    assert world["supervisor"][0]["customer_phone"] == CUSTOMER and world["supervisor"][0]["agent_phone"] == AGENT
    assert "falla técnica" in world["supervisor"][0]["reason"]
    assert world["telegram"] == [("777", {"phone": CUSTOMER, "name": "Ana", "message": Context.message_body,
                                          "fallback_sent": True})]

    # The same customer fails again within the quiet time: no second apology, no second notice
    assert await turn_failure.report(Context(), exc=RuntimeError("boom")) is False
    assert len(world["whatsapp"]) == 1 and len(world["supervisor"]) == 1 and len(world["telegram"]) == 1


async def test_a_good_turn_sends_nothing(world):
    assert await turn_failure.report(Context(), result=Result(True, True)) is False
    assert await turn_failure.report(Context(), result=Result(True, False)) is False
    assert world["whatsapp"] == [] and world["supervisor"] == [] and world["telegram"] == []


async def test_nothing_is_sent_from_an_unknown_number_or_in_the_test_chat(world):
    stranger = Context()
    stranger.recipient_phone = "+59100000000"
    assert await turn_failure.report(stranger, exc=RuntimeError("boom")) is False

    token = simulation.activate(simulation.SimulationRun(tenant_id=1, agent_id=1, session_factory=None, customer_phone=CUSTOMER))
    try:
        assert await turn_failure.report(Context(), exc=RuntimeError("boom")) is False
    finally:
        simulation.deactivate(token)
    assert world["whatsapp"] == [] and world["supervisor"] == [] and world["telegram"] == []


async def test_the_supervisor_still_hears_when_whatsapp_itself_is_down(world, monkeypatch):
    async def broken(**kwargs):
        raise RuntimeError("evolution down")

    monkeypatch.setattr(turn_failure, "send_whatsapp_message", broken)
    assert await turn_failure.report(Context(), exc=RuntimeError("boom")) is False
    assert world["telegram"][0][1]["fallback_sent"] is False


async def test_a_handler_that_breaks_never_raises(world, monkeypatch):
    async def broken(phone):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(turn_failure, "_agent_config", broken)
    assert await turn_failure.report(Context(), exc=RuntimeError("boom")) is False


async def test_the_telegram_notice_escapes_what_the_customer_wrote(monkeypatch):
    posted = []

    def handler(request: httpx.Request):
        posted.append(json.loads(request.read()))
        return httpx.Response(200, json={"ok": True})

    real = httpx.AsyncClient
    monkeypatch.setattr(settings, "telegram_bot_token", "t")
    monkeypatch.setattr(telegram_service.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    assert await telegram_service.send_turn_failure_notification(
        "777", {"phone": CUSTOMER, "name": "Ana <b>", "message": "quiero 2 <poleras> & medias", "fallback_sent": True})
    text = posted[0]["text"]
    assert posted[0]["chat_id"] == "777" and "&lt;poleras&gt; &amp; medias" in text and "Ana &lt;b&gt;" in text
    assert "<poleras>" not in text and "Le avisamos" in text


async def test_the_batch_path_reports_a_failed_turn(monkeypatch):
    """The usual path (batching on): a failed result and an exception both reach the handler."""
    from app.services.pipeline import runner as runner_module

    reported, outcome = [], {}

    class FakeRunner:
        async def run(self, context):
            if outcome["kind"] == "raise":
                raise RuntimeError("graph exploded")
            return Result(outcome["kind"] == "ok", outcome["kind"] == "ok", "stage: failed")

    async def report(context, result=None, exc=None):
        reported.append((context.sender_phone, result.error if result else None, str(exc) if exc else None))

    async def lock(*a, **k):
        return True

    async def batch(agent_phone, user_phone):
        return [{"message_sid": "a", "body": "hola", "received_at": 1.0, "profile_name": "Ana", "media_urls": []}]

    class NoRedis:
        async def srem(self, *a):
            return 0

    async def zero(*a, **k):
        return 0

    monkeypatch.setattr(runner_module, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(turn_failure, "report", report)
    monkeypatch.setattr(batch_manager, "_acquire_run_lock", lock)
    monkeypatch.setattr(batch_manager, "take_batch", batch)
    monkeypatch.setattr(batch_manager, "release_token_lock", lock)
    monkeypatch.setattr(batch_manager, "list_length", zero)
    monkeypatch.setattr(batch_manager, "get_redis_client", lambda: NoRedis())

    for kind in ("ok", "failed", "raise"):
        outcome["kind"] = kind
        await batch_manager._process_batch(AGENT, CUSTOMER)
    assert reported == [(CUSTOMER, "stage: failed", None), (CUSTOMER, None, "graph exploded")]


# ---- connection.update ----

def _update(state="close", **over):
    payload = {"event": "connection.update", "instance": "yeison-12-ab12",
               "data": {"instance": "yeison-12-ab12", "state": state, "statusReason": 401}}
    payload.update(over)
    return payload


def test_connection_update_is_read_from_both_event_spellings():
    assert evolution.connection_update(_update("close")) == ("yeison-12-ab12", "close")
    assert evolution.connection_update(_update("OPEN", event="CONNECTION_UPDATE")) == ("yeison-12-ab12", "open")
    assert evolution.connection_update(_update(event="messages.upsert")) is None
    assert evolution.connection_update({"event": "connection.update", "instance": "x", "data": {}}) is None


async def test_the_webhook_forwards_the_state_to_the_panel(monkeypatch):
    forwarded = []

    async def forward(instance, state):
        forwarded.append((instance, state))

    monkeypatch.setattr(settings, "evolution_webhook_secret", "s3cret")
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(connection_updates, "forward", forward)
    api = FastAPI()
    api.include_router(webhooks.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t") as client:
        ok = await client.post("/webhooks/evolution", content=json.dumps(_update("close")),
                               headers={"x-evolution-webhook-secret": "s3cret"})
        refused = await client.post("/webhooks/evolution", content=json.dumps(_update("close")),
                                    headers={"x-evolution-webhook-secret": "wrong"})
    assert ok.status_code == 200 and ok.json() == {"status": "ok", "event": "connection.update"}
    assert refused.status_code == 403
    assert forwarded == [("yeison-12-ab12", "close")]


async def test_forward_posts_with_the_internal_key_and_never_raises(monkeypatch):
    seen = []

    def handler(request: httpx.Request):
        seen.append((str(request.url), request.headers.get("x-internal-key"), json.loads(request.read())))
        return httpx.Response(200 if len(seen) == 1 else 404, json={})

    real = httpx.AsyncClient
    monkeypatch.setattr(settings, "backend_api_url", "http://panel:8071/api/v1/")
    monkeypatch.setattr(settings, "backend_api_key", "k")
    monkeypatch.setattr(connection_updates.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    assert await connection_updates.forward("yeison-12-ab12", "close") is True
    assert seen[0] == ("http://panel:8071/api/v1/internal/whatsapp/connection-update", "k",
                       {"instance": "yeison-12-ab12", "state": "close"})
    assert await connection_updates.forward("yeison-12-ab12", "open") is False  # an older panel answers 404

    monkeypatch.setattr(settings, "backend_api_key", None)
    assert await connection_updates.forward("yeison-12-ab12", "open") is False and len(seen) == 2
