"""
Evolution API helpers for outbound messaging and webhook normalization.
"""

from __future__ import annotations

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


def _normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    value = str(value).strip()
    if value.startswith("whatsapp:"):
        value = value.replace("whatsapp:", "", 1)
    if value.endswith("@s.whatsapp.net"):
        value = value.split("@", 1)[0]
    return value


def _extract_root(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _extract_text(payload: dict[str, Any]) -> str:
    data = _extract_root(payload)
    message = data.get("message") if isinstance(data.get("message"), dict) else {}

    candidates = [
        data.get("body"),
        data.get("text"),
        message.get("conversation") if isinstance(message, dict) else None,
        message.get("extendedTextMessage", {}).get("text") if isinstance(message, dict) else None,
        message.get("imageMessage", {}).get("caption") if isinstance(message, dict) else None,
        message.get("videoMessage", {}).get("caption") if isinstance(message, dict) else None,
        message.get("documentMessage", {}).get("caption") if isinstance(message, dict) else None,
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate)
    return ""


def _extract_media_urls(payload: dict[str, Any]) -> list[str]:
    data = _extract_root(payload)
    media_urls: list[str] = []

    for key in ("mediaUrl", "media_url", "url"):
        value = data.get(key)
        if value:
            media_urls.append(str(value))

    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    if isinstance(message, dict):
        for message_key in ("imageMessage", "videoMessage", "audioMessage", "documentMessage"):
            nested = message.get(message_key)
            if isinstance(nested, dict):
                media_url = nested.get("url") or nested.get("mediaUrl")
                if media_url:
                    media_urls.append(str(media_url))

    return media_urls


def _extract_instance_name(payload: dict[str, Any]) -> Optional[str]:
    candidates = [
        payload.get("instance"),
        payload.get("instanceName"),
        payload.get("instance_name"),
    ]
    data = _extract_root(payload)
    candidates.extend([
        data.get("instance"),
        data.get("instanceName"),
        data.get("instance_name"),
    ])

    for candidate in candidates:
        if candidate:
            return str(candidate).strip()
    return None


def _extract_sender_phone(payload: dict[str, Any]) -> Optional[str]:
    data = _extract_root(payload)
    candidates = [
        data.get("sender"),
        data.get("from"),
        data.get("participant"),
        data.get("remoteJid"),
    ]

    key = data.get("key") if isinstance(data.get("key"), dict) else {}
    if isinstance(key, dict):
        candidates.extend([key.get("participant"), key.get("remoteJid")])

    for candidate in candidates:
        normalized = _normalize_phone(candidate)
        if normalized:
            return normalized
    return None


def _extract_recipient_phone(payload: dict[str, Any]) -> Optional[str]:
    data = _extract_root(payload)
    candidates = [
        data.get("to"),
        data.get("recipient"),
        data.get("phone"),
        data.get("instancePhone"),
    ]

    key = data.get("key") if isinstance(data.get("key"), dict) else {}
    if isinstance(key, dict):
        candidates.append(key.get("remoteJid"))

    for candidate in candidates:
        normalized = _normalize_phone(candidate)
        if normalized:
            return normalized
    return None


def normalize_evolution_webhook_payload(payload: dict[str, Any]) -> WhatsAppWebhookRequest:
    """Normalize an Evolution webhook payload into the internal webhook model."""
    data = _extract_root(payload)
    key = data.get("key") if isinstance(data.get("key"), dict) else {}
    message_id = None
    if isinstance(key, dict):
        message_id = key.get("id") or key.get("messageId")
    message_id = message_id or data.get("messageId") or data.get("message_id") or data.get("id") or payload.get("id")

    instance_name = _extract_instance_name(payload)
    sender_phone = _extract_sender_phone(payload)
    recipient_phone = _extract_recipient_phone(payload)

    if not sender_phone:
        sender_phone = _normalize_phone(data.get("senderPhone") or data.get("fromNumber") or data.get("contact"))

    if not recipient_phone and instance_name:
        recipient_phone = instance_name

    return WhatsAppWebhookRequest(
        provider="evolution",
        message_id=str(message_id or ""),
        sender_phone=sender_phone or "",
        recipient_phone=recipient_phone or "",
        body=_extract_text(payload),
        profile_name=payload.get("pushName") or payload.get("profileName") or payload.get("name"),
        media_urls=_extract_media_urls(payload),
        instance_name=instance_name,
        raw_payload=payload,
    )


async def send_evolution_message(
    to: str,
    body: str,
    from_number: str,
    media_url: Optional[str] = None,
) -> str:
    """Send a message through Evolution API."""
    connection = await get_whatsapp_connection_for_phone(from_number)

    api_url = settings.evolution_api_url
    api_key = settings.evolution_api_key
    instance_name = (connection or {}).get("evolution_instance_name")

    if not api_url:
        raise ValueError("Evolution API URL is not configured.")
    if not api_key:
        raise ValueError("Evolution API key is not configured.")
    if not instance_name:
        raise ValueError("Evolution instance name is not configured.")

    to_phone = _normalize_phone(to)
    from_phone = _normalize_phone(from_number)
    headers = {
        "apikey": api_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        if media_url:
            endpoint = settings.evolution_send_media_path.format(instance=instance_name)
            payload = {
                "number": to_phone,
                "mediatype": "image",
                "media": media_url,
                "caption": body,
            }
        else:
            endpoint = settings.evolution_send_text_path.format(instance=instance_name)
            payload = {
                "number": to_phone,
                "text": body,
            }

        response = await client.post(
            f"{api_url.rstrip('/')}{endpoint}",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()

        try:
            response_data: dict[str, Any] = response.json()
        except Exception:
            response_data = {}

    message_id = (
        response_data.get("key", {}).get("id")
        or response_data.get("messageId")
        or response_data.get("message_id")
        or response_data.get("id")
        or response.headers.get("x-message-id")
        or response.headers.get("x-id")
        or f"evolution:{instance_name}:{to_phone}"
    )

    logger.info(
        "whatsapp_message_sent",
        provider="evolution",
        message_id=message_id,
        to=to_phone,
        from_=from_phone,
        instance_name=instance_name,
        has_media=bool(media_url),
    )

    return str(message_id)


async def resolve_inbound_connection(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Resolve the per-agent connection associated with an inbound Evolution payload."""
    instance_name = _extract_instance_name(payload)
    if instance_name:
        connection = await get_whatsapp_connection_for_instance(instance_name)
        if connection:
            return connection

    recipient_phone = _extract_recipient_phone(payload)
    if recipient_phone:
        return await get_whatsapp_connection_for_phone(recipient_phone)

    return None