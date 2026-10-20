"""
Evolution API helpers for outbound messaging and webhook normalization.

One shared Evolution host; each agent has its own instance (whatsapp_connection
.evolution_instance_name), linked by QR from the panel (Y-10). Inbound
webhooks (v2, event "messages.upsert") look like:

    {"event": "messages.upsert", "instance": "yeison-12-ab12",
     "data": {"key": {"remoteJid": "59170000000@s.whatsapp.net", "fromMe": false, "id": "3EB0…"},
              "pushName": "Ana", "message": {"conversation": "hola"}, "messageType": "conversation"}}

`key.remoteJid` is the *customer*. The agent is found by the instance name, never
by a phone in the payload. Messages sent by the business itself (fromMe), group
and broadcast messages and every other event are ignored.

WhatsApp media URLs are encrypted, so media is referenced as
"evolution-media://<instance>/<message id>" and downloaded decrypted through
POST /chat/getBase64FromMediaMessage/<instance>.
"""

from __future__ import annotations

import base64
from typing import Any, Optional

import httpx
import structlog

from app.core.config import settings
from app.schemas.webhook import WhatsAppWebhookRequest
from app.services.whatsapp_connections import (
    get_whatsapp_connection_for_instance,
    get_whatsapp_connection_for_phone,
)

logger = structlog.get_logger()

MEDIA_SCHEME = "evolution-media://"
MEDIA_TYPES = ("imageMessage", "documentMessage", "audioMessage", "videoMessage", "stickerMessage")
_SKIP_JIDS = ("@g.us", "@broadcast", "@newsletter")


def _normalize_phone(value: str | None) -> str:
    """'59170000000@s.whatsapp.net' / 'whatsapp:+591…' / '591…' -> '+59170000000'."""
    if not value:
        return ""
    value = str(value).strip()
    if value.startswith("whatsapp:"):
        value = value.replace("whatsapp:", "", 1)
    if "@" in value:
        value = value.split("@", 1)[0]
    if ":" in value:  # device suffix, '59170000000:12'
        value = value.split(":", 1)[0]
    digits = value.lstrip("+")
    return f"+{digits}" if digits.isdigit() else value


def _extract_root(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _key(payload: dict[str, Any]) -> dict[str, Any]:
    key = _extract_root(payload).get("key")
    return key if isinstance(key, dict) else {}


def _message(payload: dict[str, Any]) -> dict[str, Any]:
    message = _extract_root(payload).get("message")
    return message if isinstance(message, dict) else {}


def event_name(payload: dict[str, Any]) -> str:
    """'MESSAGES_UPSERT' and 'messages.upsert' both read 'messages.upsert'."""
    return str(payload.get("event") or "").strip().lower().replace("_", ".")


def skip_reason(payload: dict[str, Any]) -> Optional[str]:
    """Why an Evolution webhook is not a customer message to answer (None when it is)."""
    event = event_name(payload)
    if event and event != "messages.upsert":
        return f"event:{event}"
    key = _key(payload)
    if key.get("fromMe"):
        return "from_me"  # sent by the business itself (from the phone or by us): never answer it
    jid = str(key.get("remoteJid") or "")
    if not jid or any(jid.endswith(suffix) for suffix in _SKIP_JIDS) or jid.startswith("status@"):
        return "not_a_chat"
    if not _message(payload):
        return "no_message"
    return None


def _extract_text(payload: dict[str, Any]) -> str:
    data = _extract_root(payload)
    message = _message(payload)
    candidates = [
        message.get("conversation"),
        (message.get("extendedTextMessage") or {}).get("text"),
        (message.get("imageMessage") or {}).get("caption"),
        (message.get("videoMessage") or {}).get("caption"),
        (message.get("documentMessage") or {}).get("caption"),
        (message.get("buttonsResponseMessage") or {}).get("selectedDisplayText"),
        (message.get("listResponseMessage") or {}).get("title"),
        data.get("body"),
        data.get("text"),
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate)
    return ""


def _extract_instance_name(payload: dict[str, Any]) -> Optional[str]:
    data = _extract_root(payload)
    for candidate in (payload.get("instance"), payload.get("instanceName"), payload.get("instance_name"),
                      data.get("instance"), data.get("instanceName"), data.get("instance_name")):
        if isinstance(candidate, dict):  # some versions send {"instanceName": …}
            candidate = candidate.get("instanceName") or candidate.get("name")
        if candidate:
            return str(candidate).strip()
    return None


def _extract_sender_phone(payload: dict[str, Any]) -> str:
    """The customer. With WhatsApp's LID ids (@lid) the phone comes in remoteJidAlt / senderPn."""
    data = _extract_root(payload)
    key = _key(payload)
    jid = str(key.get("remoteJid") or "")
    if jid.endswith("@lid"):
        for alt in (key.get("remoteJidAlt"), key.get("senderPn"), data.get("senderPn"), data.get("sender")):
            if alt and not str(alt).endswith("@lid"):
                return _normalize_phone(alt)
    return _normalize_phone(jid or data.get("sender") or data.get("from"))


def _extract_media_urls(payload: dict[str, Any], instance_name: Optional[str]) -> list[str]:
    message = _message(payload)
    message_id = _key(payload).get("id")
    if instance_name and message_id and any(isinstance(message.get(t), dict) for t in MEDIA_TYPES):
        return [f"{MEDIA_SCHEME}{instance_name}/{message_id}"]
    return []


def normalize_evolution_webhook_payload(payload: dict[str, Any]) -> WhatsAppWebhookRequest:
    """Normalize an Evolution webhook payload into the internal webhook model.

    recipient_phone stays empty: the route fills it with the agent's number, found by the instance.
    """
    data = _extract_root(payload)
    key = _key(payload)
    message_id = key.get("id") or data.get("messageId") or data.get("id") or payload.get("id")
    instance_name = _extract_instance_name(payload)
    return WhatsAppWebhookRequest(
        provider="evolution",
        message_id=str(message_id or ""),
        sender_phone=_extract_sender_phone(payload),
        recipient_phone="",
        body=_extract_text(payload),
        profile_name=data.get("pushName") or payload.get("pushName") or payload.get("profileName"),
        media_urls=_extract_media_urls(payload, instance_name),
        instance_name=instance_name,
        raw_payload=payload,
    )


def _headers() -> dict[str, str]:
    return {"apikey": settings.evolution_api_key or "", "Content-Type": "application/json"}


async def send_evolution_message(
    to: str,
    body: str,
    from_number: str,
    media_url: Optional[str] = None,
) -> str:
    """Send a message through Evolution API."""
    connection = await get_whatsapp_connection_for_phone(from_number)

    api_url = settings.evolution_api_url
    instance_name = (connection or {}).get("evolution_instance_name")

    if not api_url:
        raise ValueError("Evolution API URL is not configured.")
    if not settings.evolution_api_key:
        raise ValueError("Evolution API key is not configured.")
    if not instance_name:
        raise ValueError("Evolution instance name is not configured.")

    to_phone = _normalize_phone(to).lstrip("+")  # Evolution takes the number without '+'
    async with httpx.AsyncClient(timeout=30.0) as client:
        if media_url:
            endpoint = settings.evolution_send_media_path.format(instance=instance_name)
            payload = {"number": to_phone, "mediatype": "image", "media": media_url, "caption": body}
        else:
            endpoint = settings.evolution_send_text_path.format(instance=instance_name)
            payload = {"number": to_phone, "text": body}
        response = await client.post(f"{api_url.rstrip('/')}{endpoint}", json=payload, headers=_headers())
        response.raise_for_status()
        try:
            response_data: dict[str, Any] = response.json()
        except Exception:
            response_data = {}

    message_id = (
        (response_data.get("key") or {}).get("id")
        or response_data.get("messageId")
        or response_data.get("id")
        or f"evolution:{instance_name}:{to_phone}"
    )
    logger.info("whatsapp_message_sent", provider="evolution", message_id=message_id, to=to_phone,
                instance_name=instance_name, has_media=bool(media_url))
    return str(message_id)


async def fetch_media(reference: str) -> tuple[bytes, str]:
    """Download a media message decrypted: 'evolution-media://<instance>/<message id>' -> (bytes, mimetype)."""
    if not reference.startswith(MEDIA_SCHEME):
        raise ValueError("Not an Evolution media reference")
    instance_name, _, message_id = reference[len(MEDIA_SCHEME):].partition("/")
    if not (instance_name and message_id and settings.evolution_api_url):
        raise ValueError("Incomplete Evolution media reference or Evolution not configured")
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{settings.evolution_api_url.rstrip('/')}/chat/getBase64FromMediaMessage/{instance_name}",
            json={"message": {"key": {"id": message_id}}, "convertToMp4": False},
            headers=_headers(),
        )
        response.raise_for_status()
        data = response.json()
    content = base64.b64decode(data.get("base64") or "")
    mimetype = str(data.get("mimetype") or "image/jpeg").split(";")[0].strip()
    if not content:
        raise ValueError("Evolution returned no media")
    return content, mimetype


async def resolve_inbound_connection(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The agent's connection for an inbound Evolution payload: by its instance name only."""
    instance_name = _extract_instance_name(payload)
    if not instance_name:
        return None
    return await get_whatsapp_connection_for_instance(instance_name)
