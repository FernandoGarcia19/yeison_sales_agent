"""Evolution API (Y-09): only customer messages reach the pipeline, for the agent that owns the instance."""

import base64
import json
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused/unused")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.api.v1 import webhooks  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.integrations.whatsapp import evolution  # noqa: E402

SECRET = "test-evolution-secret"


def _message(**over):
    payload = {
        "event": "messages.upsert", "instance": "yeison-12-ab12",
        "data": {"key": {"remoteJid": "59170000001@s.whatsapp.net", "fromMe": False, "id": "3EB0AA"},
                 "pushName": "Ana", "message": {"conversation": "hola, ¿tienen poleras?"},
                 "messageType": "conversation"},
    }
    for path, value in over.items():
        target = payload
        *parents, last = path.split(".")
        for p in parents:
            target = target[p]
        target[last] = value
    return payload


def test_normalization_takes_the_customer_from_remote_jid():
    data = evolution.normalize_evolution_webhook_payload(_message())
    assert data.sender_phone == "+59170000001" and data.recipient_phone == ""  # the route fills the agent
    assert data.body == "hola, ¿tienen poleras?" and data.instance_name == "yeison-12-ab12"
    assert data.ProfileName == "Ana" and not data.has_media

    lid = _message(**{"data.key": {"remoteJid": "1234567@lid", "remoteJidAlt": "59170000002@s.whatsapp.net",
                                   "fromMe": False, "id": "X"}})
    assert evolution.normalize_evolution_webhook_payload(lid).sender_phone == "+59170000002"

    photo = _message(**{"data.message": {"imageMessage": {"caption": "mi comprobante", "url": "https://mmg.whatsapp.net/x.enc"}}})
    data = evolution.normalize_evolution_webhook_payload(photo)
    assert data.body == "mi comprobante" and data.get_media_urls() == ["evolution-media://yeison-12-ab12/3EB0AA"]


def test_what_is_not_a_customer_message_is_skipped():
    assert evolution.skip_reason(_message()) is None
    assert evolution.skip_reason(_message(event="CONNECTION_UPDATE")) == "event:connection.update"
    assert evolution.skip_reason(_message(event="qrcode.updated")) == "event:qrcode.updated"
    assert evolution.skip_reason(_message(**{"data.key.fromMe": True})) == "from_me"
    assert evolution.skip_reason(_message(**{"data.key.remoteJid": "120363@g.us"})) == "not_a_chat"
    assert evolution.skip_reason(_message(**{"data.key.remoteJid": "status@broadcast"})) == "not_a_chat"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(settings, "evolution_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "debug", False)
    handled = []

    async def fake_connection(payload):
        if evolution._extract_instance_name(payload) == "yeison-12-ab12":
            return {"whatsapp_phone_number": "+59160000000", "agent_phone": "+59160000000"}
        return None

    async def fake_handle(background_tasks, webhook_data, provider):
        handled.append(webhook_data)
        return {"status": "queued"}

    monkeypatch.setattr(webhooks, "resolve_inbound_connection", fake_connection)
    monkeypatch.setattr(webhooks, "_handle_inbound_whatsapp_webhook", fake_handle)
    api = FastAPI()
    api.include_router(webhooks.router)  # the router already has /webhooks
    return api, handled


async def _post(api, payload, secret=SECRET):
    headers = {"x-evolution-webhook-secret": secret} if secret is not None else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t") as client:
        return await client.post("/webhooks/evolution", content=json.dumps(payload), headers=headers)


async def test_the_secret_is_required(app, monkeypatch):
    api, handled = app
    assert (await _post(api, _message(), secret=None)).status_code == 403
    assert (await _post(api, _message(), secret="wrong")).status_code == 403
    monkeypatch.setattr(settings, "evolution_webhook_secret", None)  # not configured: refuse everything
    assert (await _post(api, _message())).status_code == 403
    assert handled == []


async def test_messages_go_to_the_agent_of_the_instance(app):
    api, handled = app
    r = await _post(api, _message())
    assert r.status_code == 200 and len(handled) == 1
    assert handled[0].recipient_phone == "+59160000000" and handled[0].sender_phone == "+59170000001"

    for ignored in (_message(**{"data.key.fromMe": True}), _message(event="connection.update"),
                    _message(instance="yeison-otro")):
        r = await _post(api, ignored)
        assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert len(handled) == 1


async def test_media_is_downloaded_decrypted(monkeypatch):
    monkeypatch.setattr(settings, "evolution_api_url", "https://evo.test")
    monkeypatch.setattr(settings, "evolution_api_key", "k")
    seen = []

    def handler(request: httpx.Request):
        seen.append((str(request.url), request.headers.get("apikey"), json.loads(request.read())))
        return httpx.Response(200, json={"base64": base64.b64encode(b"JPEGDATA").decode(), "mimetype": "image/jpeg"})

    real = httpx.AsyncClient
    monkeypatch.setattr(evolution.httpx, "AsyncClient", lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    content, mimetype = await evolution.fetch_media("evolution-media://yeison-12-ab12/3EB0AA")
    assert (content, mimetype) == (b"JPEGDATA", "image/jpeg")
    assert seen[0][0] == "https://evo.test/chat/getBase64FromMediaMessage/yeison-12-ab12"
    assert seen[0][1] == "k" and seen[0][2]["message"]["key"]["id"] == "3EB0AA"
