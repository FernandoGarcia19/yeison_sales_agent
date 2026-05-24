from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
import structlog
from app.core.database import get_session_factory
from sqlalchemy import select
from app.models import SalesConversation, AgentInstance
from app.schemas.pipeline import ConversationState
from app.integrations.whatsapp.client import send_whatsapp_message
import httpx
from app.core.config import settings

_TELEGRAM_SECRET_HEADER = "x-telegram-bot-api-secret-token"
_TELEGRAM_CAPTION_LIMIT = 1024


def _build_caption(status_header: str, original_caption: str) -> str:
    """Prepend a status header to the original caption, truncating if needed."""
    combined = status_header + original_caption
    if len(combined) > _TELEGRAM_CAPTION_LIMIT:
        overhead = len(combined) - _TELEGRAM_CAPTION_LIMIT + 1  # +1 for ellipsis
        combined = status_header + original_caption[:-overhead] + "…"
    return combined

logger = structlog.get_logger()

router = APIRouter(prefix="/webhooks/telegram", tags=["telegram_webhooks"])


@router.post("")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Webhook to receive updates from Telegram:
    - callback_query: approve/reject inline button presses
    - message with /start <token>: Telegram bot connection flow
    """
    if settings.telegram_webhook_secret:
        incoming_secret = request.headers.get(_TELEGRAM_SECRET_HEADER, "")
        if incoming_secret != settings.telegram_webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    try:
        update_data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if "callback_query" in update_data:
        callback_query = update_data["callback_query"]
        callback_id = callback_query["id"]
        callback_data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")
        original_caption = message.get("caption", "")

        # Must answer immediately — this dismisses the loading spinner on the button.
        await _answer_callback_query(callback_id)

        if callback_data.startswith("approve_sale_") or callback_data.startswith("reject_sale_"):
            background_tasks.add_task(
                process_telegram_callback,
                callback_data=callback_data,
                chat_id=chat_id,
                message_id=message_id,
                original_caption=original_caption,
            )

    elif "message" in update_data:
        message = update_data["message"]
        text: str = (message.get("text") or "").strip()
        chat_id = message.get("chat", {}).get("id")

        # Deep-link connect: Telegram sends "/start {token}" as the message text.
        # Split on the first space — if no token present (plain /start), ignore.
        if text.startswith("/start") and chat_id:
            parts = text.split(" ", 1)
            token = parts[1].strip() if len(parts) > 1 else ""
            if token:
                background_tasks.add_task(_process_connect_start, token=token, chat_id=str(chat_id))

    return {"status": "ok"}


async def _process_connect_start(token: str, chat_id: str) -> None:
    """Validate the connect token and register the chat_id via panel_backend."""
    if not settings.backend_api_url or not settings.backend_api_key:
        logger.error("telegram_connect_missing_backend_config")
        await _send_telegram_message(
            chat_id,
            "⚠️ Error de configuración del servidor. Por favor contacta al administrador.",
        )
        return

    url = f"{settings.backend_api_url.rstrip('/')}/internal/telegram/connect"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                json={"token": token, "chat_id": chat_id},
                headers={"x-internal-key": settings.backend_api_key},
            )

        if response.status_code == 200:
            logger.info("telegram_chat_registered", chat_id=chat_id)
            await _send_telegram_message(
                chat_id,
                "✅ ¡Conexión exitosa! Este chat recibirá las notificaciones de aprobación de pagos.",
            )
        elif response.status_code == 400:
            detail = response.json().get("detail", "Token inválido o expirado")
            logger.warning("telegram_connect_bad_token", chat_id=chat_id, detail=detail)
            await _send_telegram_message(
                chat_id,
                f"❌ No se pudo conectar: {detail}. Genera un nuevo enlace desde el panel.",
            )
        else:
            logger.error("telegram_connect_unexpected_status", status=response.status_code, chat_id=chat_id)
            await _send_telegram_message(
                chat_id,
                "⚠️ Error interno al conectar. Por favor intenta de nuevo.",
            )

    except Exception as e:
        logger.error("telegram_connect_request_failed", error=str(e), chat_id=chat_id)
        await _send_telegram_message(
            chat_id,
            "⚠️ Error al comunicarse con el servidor. Por favor intenta de nuevo.",
        )


async def _send_telegram_message(chat_id: str, text: str) -> None:
    if not settings.telegram_bot_token:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json={"chat_id": chat_id, "text": text})
    except Exception as e:
        logger.error("telegram_send_message_failed", error=str(e))


async def _answer_callback_query(callback_id: str, text: str = "") -> None:
    """Acknowledge the button press so Telegram removes the loading spinner."""
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/answerCallbackQuery"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json={"callback_query_id": callback_id, "text": text})
    except Exception as e:
        logger.error("telegram_answer_callback_failed", error=str(e))


async def process_telegram_callback(callback_data: str, chat_id: int, message_id: int, original_caption: str = ""):
    """
    Process the callback query from Telegram inline buttons.
    Updates the SalesConversation and sends a WhatsApp message.
    """
    action, conv_id_str = callback_data.rsplit("_", 1)
    try:
        conversation_id = int(conv_id_str)
    except ValueError:
        logger.error("telegram_callback_invalid_id", callback_data=callback_data)
        return

    session_factory = get_session_factory()

    async with session_factory() as db:
        # Load conversation
        stmt = select(SalesConversation).where(SalesConversation.id == conversation_id)
        result = await db.execute(stmt)
        conversation = result.scalar_one_or_none()

        if not conversation:
            logger.error("telegram_callback_conversation_not_found", conversation_id=conversation_id)
            return

        # Load agent for phone number
        agent_stmt = select(AgentInstance).where(AgentInstance.id == conversation.agent_instance_id)
        agent_result = await db.execute(agent_stmt)
        agent = agent_result.scalar_one_or_none()

        if not agent:
            logger.error("telegram_callback_agent_not_found", conversation_id=conversation_id)
            return

        user_phone = conversation.external_user_id
        from_phone = agent.phone_number

        if action == "approve_sale":
            # Idempotency: only process if we're still waiting for receipt approval
            if conversation.current_state == ConversationState.ORDER_COMPLETED:
                logger.info("telegram_callback_already_approved", conversation_id=conversation_id)
                return

            conversation.current_state = ConversationState.ORDER_COMPLETED
            await db.commit()

            msg = "✅ ¡Tu pago ha sido aprobado! Tu pedido está siendo procesado."
            await send_whatsapp_message(to=user_phone, body=msg, from_number=from_phone)

            if chat_id and message_id:
                status_header = "✅ <b>PAGO APROBADO</b> — El usuario ha sido notificado.\n\n"
                await edit_telegram_message_text(chat_id, message_id, _build_caption(status_header, original_caption))

        elif action == "reject_sale":
            # Idempotency: only process if we're still waiting for receipt approval
            if conversation.current_state != ConversationState.AWAITING_RECEIPT:
                logger.info("telegram_callback_already_processed", conversation_id=conversation_id, state=conversation.current_state)
                return

            # No state change — keep AWAITING_RECEIPT so user can re-send proof.
            msg = "❌ Lo sentimos, no pudimos verificar tu pago. Por favor, envía nuevamente el comprobante."
            await send_whatsapp_message(to=user_phone, body=msg, from_number=from_phone)

            if chat_id and message_id:
                status_header = "❌ <b>PAGO RECHAZADO</b> — El usuario puede reenviar el comprobante.\n\n"
                await edit_telegram_message_text(chat_id, message_id, _build_caption(status_header, original_caption))


async def edit_telegram_message_text(chat_id: int, message_id: int, new_text: str):
    if not settings.telegram_bot_token:
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/editMessageCaption"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "caption": new_text,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": []},
    }
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload)
    except Exception as e:
        logger.error("telegram_edit_message_failed", error=str(e))
