"""Private messages and dir_* buttons go to Yeison, the Director, in panel_backend (Y-33)."""

import json
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused/unused")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.api.v1 import telegram_webhook as tw  # noqa: E402
from app.core.config import settings  # noqa: E402

_RealAsyncClient = httpx.AsyncClient


@pytest.fixture
def captured(monkeypatch):
    calls = {"panel": [], "sent": [], "edited": [], "answer": {"text": "📈 Ventas", "buttons": [], "linked": True}}

    async def fake_send(chat_id, text, reply_markup=None):
        calls["sent"].append((chat_id, text, reply_markup))

    async def fake_replace(chat_id, message_id, text):
        calls["edited"].append((chat_id, message_id, text))
        return True

    async def fake_answer(callback_id, text=""):
        return None

    def panel_handler(request: httpx.Request):
        calls["panel"].append((str(request.url), request.headers.get("x-internal-key"), json.loads(request.read())))
        return httpx.Response(200, json=calls["answer"])

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(panel_handler)
        return _RealAsyncClient(*args, **kwargs)

    monkeypatch.setattr(tw, "_send_telegram_message", fake_send)
    monkeypatch.setattr(tw, "_replace_text_message", fake_replace)
    monkeypatch.setattr(tw, "_answer_callback_query", fake_answer)
    monkeypatch.setattr(tw.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(settings, "backend_api_url", "http://panel/api/v1/")
    monkeypatch.setattr(settings, "backend_api_key", "internal-key")
    monkeypatch.setattr(settings, "telegram_webhook_secret", None, raising=False)
    return calls


async def _post(update):
    app = FastAPI()
    app.include_router(tw.router)
    async with _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/webhooks/telegram", json=update)


def _message(text, chat_type="private", user_id=555):
    return {"update_id": 1, "message": {"message_id": 9, "text": text, "chat": {"id": user_id, "type": chat_type},
                                        "from": {"id": user_id}}}


async def test_private_message_is_answered_by_the_director(captured):
    assert (await _post(_message("¿cuánto vendí hoy?"))).status_code == 200
    url, key, body = captured["panel"][0]
    assert url == "http://panel/api/v1/internal/director/message" and key == "internal-key"
    assert body == {"telegram_user_id": 555, "chat_id": "555", "text": "¿cuánto vendí hoy?"}
    assert captured["sent"] == [("555", "📈 Ventas", None)]


async def test_buttons_become_an_inline_keyboard(captured):
    captured["answer"] = {"text": "¿Confirmas?", "linked": True, "buttons": [[
        {"text": "✅ Confirmar", "callback_data": "dir_ok_ab"}, {"text": "❌ Cancelar", "callback_data": "dir_no_ab"}]]}
    await _post(_message("llegaron 5 unidades de polera"))
    _, _, markup = captured["sent"][0]
    assert markup == {"inline_keyboard": [[{"text": "✅ Confirmar", "callback_data": "dir_ok_ab"},
                                           {"text": "❌ Cancelar", "callback_data": "dir_no_ab"}]]}


async def test_plain_start_greets_and_groups_are_ignored(captured):
    await _post(_message("/start"))
    assert captured["panel"][0][2]["text"] == "/start"
    await _post(_message("hola a todos", chat_type="group", user_id=-100))
    assert len(captured["panel"]) == 1  # the supervisor's group chat is not answered


async def test_director_button_is_applied_and_replaces_the_buttons(captured):
    captured["answer"] = {"text": "✅ Listo.", "buttons": [], "linked": True}
    update = {"update_id": 2, "callback_query": {
        "id": "cb1", "data": "dir_ok_ab", "from": {"id": 555},
        "message": {"message_id": 42, "text": "📝 ¿Confirmas?", "chat": {"id": 555, "type": "private"}}}}
    assert (await _post(update)).status_code == 200
    url, _, body = captured["panel"][0]
    assert url == "http://panel/api/v1/internal/director/callback"
    assert body == {"telegram_user_id": 555, "chat_id": "555", "data": "dir_ok_ab"}
    assert captured["edited"] == [("555", 42, "📝 ¿Confirmas?\n\n✅ Listo.")]


async def test_panel_down_still_answers(captured, monkeypatch):
    def broken(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda request: httpx.Response(500))
        return _RealAsyncClient(*args, **kwargs)

    monkeypatch.setattr(tw.httpx, "AsyncClient", broken)
    await _post(_message("/ventas"))
    assert "No pude responder" in captured["sent"][0][1]


async def test_invoice_photo_is_downloaded_and_sent_to_the_director(captured, monkeypatch):
    async def fake_download(file_id):
        assert file_id == "big"  # the largest size
        return b"jpeg-bytes"

    monkeypatch.setattr(tw, "_download_telegram_file", fake_download)
    captured["answer"] = {"text": "🧾 ¿Registro este gasto?", "linked": True,
                          "buttons": [[{"text": "✅ Confirmar", "callback_data": "dir_ok_cd"}]]}
    update = {"update_id": 3, "message": {"message_id": 10, "chat": {"id": 555, "type": "private"}, "from": {"id": 555},
                                          "caption": "embalaje", "photo": [{"file_id": "small"}, {"file_id": "big"}]}}
    assert (await _post(update)).status_code == 200
    url, _, body = captured["panel"][0]
    assert url == "http://panel/api/v1/internal/director/photo"
    assert body["image_base64"] == "anBlZy1ieXRlcw==" and body["caption"] == "embalaje"
    assert captured["sent"][0][1] == "🔎 Leyendo la factura…"
    assert captured["sent"][1][2] == {"inline_keyboard": [[{"text": "✅ Confirmar", "callback_data": "dir_ok_cd"}]]}


async def test_photos_in_groups_are_ignored(captured):
    update = {"update_id": 4, "message": {"message_id": 11, "chat": {"id": -100, "type": "group"}, "from": {"id": 1},
                                          "photo": [{"file_id": "x"}]}}
    await _post(update)
    assert captured["panel"] == [] and captured["sent"] == []
