from typing import Optional

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
import structlog
from app.core.database import get_session_factory
from sqlalchemy import select
from app.models import SalesConversation, AgentInstance
from app.schemas.pipeline import ConversationState
from app.integrations.whatsapp.client import send_whatsapp_message
import httpx
from app.core.config import settings
from app.services.sale_recorder import record_sale

_TELEGRAM_SECRET_HEADER = "x-telegram-bot-api-secret-token"
_TELEGRAM_CAPTION_LIMIT = 1024
# Member links from the panel (Y-32) look like "m_<hex>"; supervisor links are "{agent_id}_<hex>"
_MEMBER_TOKEN_PREFIX = "m_"
_ROLE_NAMES = {"owner": "dueño", "manager": "encargado", "accountant": "contador", "employee": "empleado"}
_DIRECTOR_CALLBACK_PREFIX = "dir_"


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
    - any other private message, and dir_* buttons: Yeison, the Director (panel_backend)
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
        elif callback_data.startswith(_DIRECTOR_CALLBACK_PREFIX) and chat_id:
            background_tasks.add_task(
                _forward_director_callback,
                telegram_user_id=(callback_query.get("from") or {}).get("id"),
                chat_id=str(chat_id),
                message_id=message_id,
                original_text=message.get("text", ""),
                data=callback_data,
            )

    elif "message" in update_data:
        message = update_data["message"]
        text: str = (message.get("text") or "").strip()
        chat_id = message.get("chat", {}).get("id")

        # Deep-link connect: Telegram sends "/start {token}" as the message text.
        # Split on the first space; a plain /start (no token) gets the Director's welcome.
        if text.startswith("/start") and chat_id:
            parts = text.split(" ", 1)
            token = parts[1].strip() if len(parts) > 1 else ""
            if token.startswith(_MEMBER_TOKEN_PREFIX):
                sender = message.get("from") or {}
                background_tasks.add_task(
                    _process_member_connect,
                    token=token,
                    chat_id=str(chat_id),
                    telegram_user_id=sender.get("id"),
                    username=sender.get("username"),
                    language_code=sender.get("language_code"),
                )
            elif token:
                background_tasks.add_task(_process_connect_start, token=token, chat_id=str(chat_id))
            elif _is_private(message):
                background_tasks.add_task(_forward_to_director, telegram_user_id=(message.get("from") or {}).get("id"),
                                          chat_id=str(chat_id), text=text)
        elif text and chat_id and _is_private(message):
            # Everything else a person writes to the bot goes to Yeison, the Director (Y-33).
            # Group chats (the supervisor's) only use buttons, so they are not answered.
            background_tasks.add_task(_forward_to_director, telegram_user_id=(message.get("from") or {}).get("id"),
                                      chat_id=str(chat_id), text=text)

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


async def _process_member_connect(token: str, chat_id: str, telegram_user_id, username, language_code) -> None:
    """Link this Telegram account to a person of the business via panel_backend (Y-32)."""
    if not settings.backend_api_url or not settings.backend_api_key or not telegram_user_id:
        logger.error("telegram_member_connect_missing_config_or_user")
        await _send_telegram_message(chat_id, "⚠️ No se pudo vincular la cuenta. Intenta de nuevo más tarde.")
        return

    url = f"{settings.backend_api_url.rstrip('/')}/internal/telegram/member-connect"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                json={"token": token, "telegram_user_id": telegram_user_id, "chat_id": chat_id,
                      "username": username, "language_code": language_code},
                headers={"x-internal-key": settings.backend_api_key},
            )
    except Exception as e:
        logger.error("telegram_member_connect_request_failed", error=str(e), chat_id=chat_id)
        await _send_telegram_message(chat_id, "⚠️ Error al comunicarse con el servidor. Intenta de nuevo.")
        return

    if response.status_code == 200:
        data = response.json()
        role = _ROLE_NAMES.get(data.get("role"), data.get("role"))
        logger.info("telegram_member_linked", chat_id=chat_id, role=data.get("role"))
        await _send_telegram_message(
            chat_id,
            f"✅ ¡Hola {data.get('member_name')}! Quedaste vinculado a {data.get('tenant_name')} como {role}.",
        )
    elif response.status_code == 400:
        await _send_telegram_message(
            chat_id, "❌ El enlace no es válido o ya venció. Pide uno nuevo en el panel de Yeison.")
    else:
        logger.error("telegram_member_connect_unexpected_status", status=response.status_code, chat_id=chat_id)
        await _send_telegram_message(chat_id, "⚠️ Error interno al vincular. Intenta de nuevo.")


def _is_private(message: dict) -> bool:
    return (message.get("chat") or {}).get("type") == "private"


async def _ask_director(path: str, body: dict) -> Optional[dict]:
    """POST to the panel's Director; None (and a log line) when it cannot answer."""
    if not settings.backend_api_url or not settings.backend_api_key:
        logger.error("director_missing_backend_config")
        return None
    url = f"{settings.backend_api_url.rstrip('/')}/internal/director/{path}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=body, headers={"x-internal-key": settings.backend_api_key})
        if response.status_code == 200:
            return response.json()
        logger.error("director_unexpected_status", path=path, status=response.status_code, body=response.text[:200])
    except Exception as e:
        logger.error("director_request_failed", path=path, error=str(e))
    return None


def _inline_keyboard(buttons: list) -> Optional[dict]:
    if not buttons:
        return None
    return {"inline_keyboard": [[{"text": b["text"], "callback_data": b["callback_data"]} for b in row]
                                for row in buttons]}


async def _forward_to_director(telegram_user_id, chat_id: str, text: str) -> None:
    if not telegram_user_id:
        return
    answer = await _ask_director("message", {"telegram_user_id": telegram_user_id, "chat_id": chat_id, "text": text})
    if answer is None:
        await _send_telegram_message(chat_id, "⚠️ No pude responder ahora. Intenta de nuevo en un momento.")
        return
    await _send_telegram_message(chat_id, answer["text"], reply_markup=_inline_keyboard(answer.get("buttons")))


async def _forward_director_callback(telegram_user_id, chat_id: str, message_id: int, original_text: str,
                                     data: str) -> None:
    """Confirmar / Cancelar of a Director action: apply it and replace the buttons with the result."""
    if not telegram_user_id:
        return
    answer = await _ask_director("callback", {"telegram_user_id": telegram_user_id, "chat_id": chat_id, "data": data})
    result = answer["text"] if answer else "⚠️ No pude procesar el botón. Intenta de nuevo."
    combined = f"{original_text}\n\n{result}".strip()
    if not message_id or not await _replace_text_message(chat_id, message_id, combined):
        await _send_telegram_message(chat_id, result)


async def _replace_text_message(chat_id: str, message_id: int, text: str) -> bool:
    """Edit a plain-text bot message and drop its buttons. False if Telegram refused."""
    if not settings.telegram_bot_token:
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/editMessageText"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json={"chat_id": chat_id, "message_id": message_id, "text": text[:4096],
                                                    "reply_markup": {"inline_keyboard": []}})
        return response.status_code == 200
    except Exception as e:
        logger.error("telegram_edit_text_failed", error=str(e))
        return False


async def _discount_stock(tenant_id: int, sale_id: int, sale_items: list) -> None:
    """Ask panel_backend to record the SALE_EXIT movements of a sale. Failures are logged, never raised."""
    if not settings.backend_api_url or not settings.backend_api_key:
        logger.error("stock_exit_missing_backend_config", sale_id=sale_id)
        return
    items = [{"product_id": i.get("product_id"), "quantity": i.get("quantity")}
             for i in sale_items or [] if i.get("product_id")]
    if not items:
        return
    url = f"{settings.backend_api_url.rstrip('/')}/internal/inventory/sale-exit"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json={"tenant_id": tenant_id, "sale_id": sale_id, "items": items},
                                         headers={"x-internal-key": settings.backend_api_key})
        if response.status_code != 200:
            logger.error("stock_exit_failed", sale_id=sale_id, status=response.status_code, body=response.text[:200])
        else:
            logger.info("stock_exit_recorded", sale_id=sale_id, movements=len(response.json().get("movements", [])))
    except Exception as e:
        logger.error("stock_exit_request_failed", sale_id=sale_id, error=str(e))


async def _send_telegram_message(chat_id: str, text: str, reply_markup: Optional[dict] = None) -> None:
    if not settings.telegram_bot_token:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json=payload)
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

        payment_method = (conversation.cart_contents or {}).get("payment_method", "qr")
        is_physical = payment_method == "physical"

        if action == "approve_sale":
            # Idempotency: only process if we're still waiting for receipt approval
            if conversation.current_state == ConversationState.ORDER_COMPLETED:
                logger.info("telegram_callback_already_approved", conversation_id=conversation_id)
                return

            conversation.current_state = ConversationState.ORDER_COMPLETED
            # Y-12: the approved order becomes a sale. A failure here must not block the
            # approval the customer is waiting for, so it runs in a savepoint and is logged.
            sale = None
            try:
                async with db.begin_nested():
                    sale = await record_sale(db, conversation, agent,
                                             payment_method="physical" if is_physical else "qr")
            except Exception as e:
                logger.error("sale_record_failed", conversation_id=conversation_id, error=str(e))
            await db.commit()
            if sale:
                # Y-36: discount stock through the panel's inventory ledger (idempotent per sale)
                await _discount_stock(sale.tenant_id, sale.id, sale.sale_items)

            if is_physical:
                msg = "✅ ¡Tu compra ha sido confirmada! Nuestro equipo se pondrá en contacto contigo para coordinar el pago y la entrega. ¡Gracias! 🎉"
                status_header = "✅ <b>PEDIDO APROBADO (PAGO FÍSICO)</b> — El usuario ha sido notificado.\n\n"
            else:
                msg = "✅ ¡Tu pago ha sido aprobado! Tu pedido está siendo procesado."
                status_header = "✅ <b>PAGO APROBADO</b> — El usuario ha sido notificado.\n\n"

            await send_whatsapp_message(to=user_phone, body=msg, from_number=from_phone)

            if chat_id and message_id:
                await edit_telegram_message_text(chat_id, message_id, _build_caption(status_header, original_caption))

        elif action == "reject_sale":
            # Idempotency: only process if we're still waiting for receipt/approval
            if conversation.current_state not in (
                ConversationState.AWAITING_RECEIPT,
                ConversationState.AWAITING_PAYMENT_METHOD,
            ):
                logger.info("telegram_callback_already_processed", conversation_id=conversation_id, state=conversation.current_state)
                return

            if is_physical:
                # Physical rejection: order cancelled — reset to BROWSING
                conversation.current_state = ConversationState.BROWSING
                await db.commit()
                msg = "❌ Lo sentimos, no podemos procesar tu pedido en este momento. Por favor, contáctanos si deseas más información."
                status_header = "❌ <b>PEDIDO RECHAZADO (PAGO FÍSICO)</b> — El usuario ha sido notificado.\n\n"
            else:
                # QR rejection: keep AWAITING_RECEIPT so user can re-send proof
                msg = "❌ Lo sentimos, no pudimos verificar tu pago. Por favor, envía nuevamente el comprobante."
                status_header = "❌ <b>PAGO RECHAZADO</b> — El usuario puede reenviar el comprobante.\n\n"

            await send_whatsapp_message(to=user_phone, body=msg, from_number=from_phone)

            if chat_id and message_id:
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
