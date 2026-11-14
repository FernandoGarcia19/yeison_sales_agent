"""The Telegram webhook only takes updates that carry the secret (Y-96)."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused/unused")

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.api.v1 import telegram_webhook as tw  # noqa: E402
from app.core.config import settings  # noqa: E402

UPDATE = {"update_id": 1, "message": {"message_id": 5, "text": "hola", "chat": {"id": 1, "type": "private"},
                                      "from": {"id": 1}}}


async def _post(headers=None):
    app = FastAPI()
    app.include_router(tw.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/webhooks/telegram", json=UPDATE, headers=headers or {})


async def test_without_a_configured_secret_everything_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "telegram_webhook_secret", None, raising=False)
    monkeypatch.setattr(settings, "debug", False)
    assert (await _post()).status_code == 503


async def test_a_wrong_or_missing_secret_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "telegram_webhook_secret", "tg-secret", raising=False)
    assert (await _post()).status_code == 401
    assert (await _post({"X-Telegram-Bot-Api-Secret-Token": "otro"})).status_code == 401
