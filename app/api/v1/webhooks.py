"""
Webhook endpoints for receiving WhatsApp messages.

Evolution API is the primary provider. Twilio remains as deprecated
compatibility support.
"""

from datetime import datetime

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Form
from fastapi.responses import Response

from app.core.config import settings
from app.core.redis_client import is_msg_duplicate, set_msg_dedup
from app.integrations.whatsapp.evolution import normalize_evolution_webhook_payload, resolve_inbound_connection
from app.integrations.whatsapp.validator import validate_evolution_webhook, validate_twilio_signature
from app.schemas.pipeline import PipelineContext
from app.schemas.webhook import TwilioWebhookRequest, WhatsAppWebhookRequest
from app.services.batch_manager import enqueue_message
from app.services.pipeline.runner import PipelineRunner
from app.services.whatsapp_connections import get_whatsapp_connection_for_instance, get_whatsapp_connection_for_phone

logger = structlog.get_logger()

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _build_form_media_urls(form_data: dict[str, str]) -> list[str]:
    media_urls: list[str] = []
    for index in range(5):
        media_url = form_data.get(f"MediaUrl{index}")
        if media_url:
            media_urls.append(media_url)
    return media_urls


async def _handle_inbound_whatsapp_webhook(
    background_tasks: BackgroundTasks,
    webhook_data: WhatsAppWebhookRequest,
    provider: str,
):
    if not settings.batch_enabled:
        if await is_msg_duplicate(webhook_data.MessageSid):
            logger.warning("duplicate_message_ignored", message_id=webhook_data.MessageSid)
            return Response(content="<Response></Response>", status_code=200, media_type="application/xml")
        await set_msg_dedup(webhook_data.MessageSid, ttl=300)

    if settings.batch_enabled:
        try:
            await enqueue_message(
                agent_phone=webhook_data.recipient_phone,
                user_phone=webhook_data.sender_phone,
                message_sid=webhook_data.MessageSid,
                body=webhook_data.Body,
                profile_name=webhook_data.ProfileName,
                media_urls=webhook_data.get_media_urls(),
            )
            logger.info(
                "message_enqueued_for_batching",
                message_id=webhook_data.MessageSid,
                provider=provider,
            )
        except Exception as exc:
            logger.error(
                "batch_enqueue_failed",
                message_id=webhook_data.MessageSid,
                error=str(exc),
                provider=provider,
            )
            background_tasks.add_task(process_message_pipeline, webhook_data=webhook_data)
    else:
        background_tasks.add_task(process_message_pipeline, webhook_data=webhook_data)

    return Response(content="<Response></Response>", status_code=200, media_type="application/xml")


@router.get("/evolution")
async def verify_webhook():
    """Evolution webhook verification endpoint."""
    return {"status": "webhook verified"}


@router.post("/evolution")
async def receive_evolution_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive WhatsApp messages from Evolution webhook."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    webhook_data = normalize_evolution_webhook_payload(payload)

    is_valid = await validate_evolution_webhook(request=request, payload=payload)
    if not is_valid:
        logger.warning("invalid_evolution_webhook", message_id=webhook_data.MessageSid)
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    if not webhook_data.recipient_phone:
        connection = await resolve_inbound_connection(payload)
        if connection:
            webhook_data.recipient_phone = connection.get("whatsapp_phone_number") or connection.get("agent_phone") or webhook_data.recipient_phone

    if not webhook_data.recipient_phone and webhook_data.instance_name:
        connection = await get_whatsapp_connection_for_instance(webhook_data.instance_name)
        if connection:
            webhook_data.recipient_phone = connection.get("whatsapp_phone_number") or connection.get("agent_phone") or webhook_data.recipient_phone

    if not webhook_data.recipient_phone:
        connection = await get_whatsapp_connection_for_phone(webhook_data.sender_phone)
        if connection:
            webhook_data.recipient_phone = connection.get("whatsapp_phone_number") or connection.get("agent_phone") or webhook_data.recipient_phone

    logger.info(
        "whatsapp_webhook_received",
        provider=webhook_data.provider,
        message_id=webhook_data.MessageSid,
        from_number=webhook_data.sender_phone,
        to_number=webhook_data.recipient_phone,
        has_media=webhook_data.has_media,
    )

    return await _handle_inbound_whatsapp_webhook(
        background_tasks=background_tasks,
        webhook_data=webhook_data,
        provider="evolution",
    )


@router.post("/twilio")
async def receive_twilio_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    MessageSid: str = Form(...),
    SmsSid: str = Form(...),
    AccountSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(default=""),
    NumMedia: str = Form(default="0"),
    ProfileName: str = Form(default=None),
    WaId: str = Form(default=None),
    MessagingServiceSid: str = Form(default=None),
    Latitude: str = Form(default=None),
    Longitude: str = Form(default=None),
    MediaUrl0: str = Form(default=None),
    MediaContentType0: str = Form(default=None),
    MediaUrl1: str = Form(default=None),
    MediaContentType1: str = Form(default=None),
    MediaUrl2: str = Form(default=None),
    MediaContentType2: str = Form(default=None),
    MediaUrl3: str = Form(default=None),
    MediaContentType3: str = Form(default=None),
    MediaUrl4: str = Form(default=None),
    MediaContentType4: str = Form(default=None),
):
    """Deprecated Twilio compatibility webhook."""
    form_data = await request.form()
    is_valid = await validate_twilio_signature(request=request, form_data=dict(form_data))
    if not is_valid:
        raise HTTPException(status_code=403, detail="Invalid signature")

    webhook_data = TwilioWebhookRequest(
        provider="twilio",
        message_id=MessageSid,
        sender_phone=From.replace("whatsapp:", ""),
        recipient_phone=To.replace("whatsapp:", ""),
        body=Body,
        profile_name=ProfileName,
        media_urls=_build_form_media_urls({
            "MediaUrl0": MediaUrl0,
            "MediaUrl1": MediaUrl1,
            "MediaUrl2": MediaUrl2,
            "MediaUrl3": MediaUrl3,
            "MediaUrl4": MediaUrl4,
        }),
        instance_name=MessagingServiceSid,
        raw_payload=dict(form_data),
    )

    logger.warning("deprecated_twilio_webhook_received", message_id=webhook_data.MessageSid)
    return await _handle_inbound_whatsapp_webhook(
        background_tasks=background_tasks,
        webhook_data=webhook_data,
        provider="twilio",
    )


async def process_message_pipeline(webhook_data: WhatsAppWebhookRequest):
    """Process incoming message through the pipeline."""
    start_time = datetime.utcnow()

    try:
        context = PipelineContext(
            message_sid=webhook_data.MessageSid,
            sender_phone=webhook_data.sender_phone,
            recipient_phone=webhook_data.recipient_phone,
            message_body=webhook_data.Body,
            profile_name=webhook_data.ProfileName,
            media_urls=webhook_data.get_media_urls(),
        )

        pipeline_runner = PipelineRunner()
        result = await pipeline_runner.run(context)

        processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000

        logger.info(
            "pipeline_completed",
            message_id=webhook_data.MessageSid,
            success=result.success,
            intent=result.intent,
            action=result.action_executed,
            processing_time_ms=processing_time,
        )

    except Exception as exc:
        processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000

        logger.error(
            "pipeline_failed",
            message_id=webhook_data.MessageSid,
            error=str(exc),
            error_type=type(exc).__name__,
            processing_time_ms=processing_time,
        )

        # TODO: Send error notification to monitoring system
        # TODO: Send fallback message to user