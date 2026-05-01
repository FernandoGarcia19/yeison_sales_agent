from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
import structlog
from app.core.database import get_session_factory
from sqlalchemy import select, update
from app.models import SalesConversation, AgentInstance
from app.schemas.pipeline import ConversationState
from app.integrations.whatsapp.client import send_whatsapp_message
import httpx
from app.core.config import settings

logger = structlog.get_logger()

router = APIRouter(prefix="/webhooks/telegram", tags=["telegram_webhooks"])

@router.post("")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Webhook to receive updates from Telegram, notably callback queries from
    inline keyboards (e.g., approve_sale or reject_sale).
    """
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

        # Must answer immediately — this dismisses the loading spinner on the button.
        await _answer_callback_query(callback_id)

        if callback_data.startswith("approve_sale_") or callback_data.startswith("reject_sale_"):
            background_tasks.add_task(
                process_telegram_callback,
                callback_data=callback_data,
                chat_id=chat_id,
                message_id=message_id,
            )

    return {"status": "ok"}


async def _answer_callback_query(callback_id: str, text: str = "") -> None:
    """Acknowledge the button press so Telegram removes the loading spinner."""
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/answerCallbackQuery"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json={"callback_query_id": callback_id, "text": text})
    except Exception as e:
        logger.error("telegram_answer_callback_failed", error=str(e))


async def process_telegram_callback(callback_data: str, chat_id: int, message_id: int):
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

            # Send WhatsApp Confirmation
            msg = "✅ ¡Tu pago ha sido aprobado! Tu pedido está siendo procesado."
            await send_whatsapp_message(
                to=user_phone,
                body=msg,
                from_number=from_phone,
            )

            # Update Telegram Message to reflect approval and remove buttons
            if chat_id and message_id:
                await edit_telegram_message_text(chat_id, message_id, "✅ <b>Pago Aprobado</b>\nEl usuario ha sido notificado.")

        elif action == "reject_sale":
            # Idempotency: only process if we're still waiting for receipt approval
            if conversation.current_state != ConversationState.AWAITING_RECEIPT:
                logger.info("telegram_callback_already_processed", conversation_id=conversation_id, state=conversation.current_state)
                return

            # No state change — keep AWAITING_RECEIPT so user can re-send proof.
            # Button removal in edit_telegram_message_text prevents further clicks.

            # Send WhatsApp Rejection
            msg = "❌ Lo sentimos, no pudimos verificar tu pago. Por favor, envía nuevamente el comprobante."
            await send_whatsapp_message(
                to=user_phone,
                body=msg,
                from_number=from_phone,
            )

            # Update Telegram Message to reflect rejection and remove buttons
            if chat_id and message_id:
                await edit_telegram_message_text(chat_id, message_id, "❌ <b>Pago Rechazado</b>\nEl usuario ha sido notificado y puede reenviar el comprobante.")

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
