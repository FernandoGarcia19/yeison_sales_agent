"""`/start m_<token>` links a person of the business through panel_backend (Y-32)."""

import json
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused/unused")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.api.v1 import telegram_webhook as tw  # noqa: E402
from app.core.config import settings  # noqa: E402

pytestmark = pytest.mark.asyncio

_RealAsyncClient = httpx.AsyncClient  # the fixture patches httpx.AsyncClient for the panel calls


@pytest.fixture
def captured(monkeypatch):
    """Capture panel calls and Telegram replies instead of hitting the network."""
    calls = {"panel": [], "sent": [], "supervisor": []}
    panel_status = {"code": 200}

    async def fake_send(chat_id, text):
        calls["sent"].append((chat_id, text))

    async def fake_supervisor(token, chat_id):
        calls["supervisor"].append(token)

    def panel_handler(request: httpx.Request):
        calls["panel"].append((str(request.url), request.headers.get("x-internal-key"), request.read()))
        if panel_status["code"] == 200:
            return httpx.Response(200, json={"tenant_name": "Zapatería El Paso", "member_name": "Luis",
                                             "role": "employee"})
        return httpx.Response(panel_status["code"], json={"detail": "Invalid or expired link"})

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(panel_handler)
        return _RealAsyncClient(*args, **kwargs)

    monkeypatch.setattr(tw, "_send_telegram_message", fake_send)
    monkeypatch.setattr(tw, "_process_connect_start", fake_supervisor)
    monkeypatch.setattr(tw.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(settings, "backend_api_url", "http://panel/api/v1/")
    monkeypatch.setattr(settings, "backend_api_key", "internal-key")
    monkeypatch.setattr(settings, "telegram_webhook_secret", None, raising=False)
    calls["panel_status"] = panel_status
    return calls


async def _post_update(text, user_id=777000111):
    app = FastAPI()
    app.include_router(tw.router)
    update = {"update_id": 1, "message": {"message_id": 5, "text": text, "chat": {"id": user_id},
                                          "from": {"id": user_id, "username": "luis_tg", "language_code": "es"}}}
    async with _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/webhooks/telegram", json=update)


async def test_member_link_calls_panel_and_greets(captured):
    r = await _post_update("/start m_" + "a" * 40)
    assert r.status_code == 200
    url, key, body = captured["panel"][0]
    assert url == "http://panel/api/v1/internal/telegram/member-connect"
    assert key == "internal-key"
    sent = json.loads(body)
    assert sent["telegram_user_id"] == 777000111 and sent["username"] == "luis_tg"
    assert captured["sent"] == [("777000111", "✅ ¡Hola Luis! Quedaste vinculado a Zapatería El Paso como empleado.")]
    assert captured["supervisor"] == []


async def test_invalid_member_link_explains_what_to_do(captured):
    captured["panel_status"]["code"] = 400
    await _post_update("/start m_" + "b" * 40)
    assert "ya venció" in captured["sent"][0][1]


async def test_supervisor_tokens_keep_their_flow(captured):
    await _post_update("/start 12_" + "c" * 32)
    assert captured["supervisor"] == ["12_" + "c" * 32]
    assert captured["panel"] == []
