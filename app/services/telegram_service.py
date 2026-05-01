import json
import httpx
import structlog
from app.core.config import settings

logger = structlog.get_logger()

_CONTENT_TYPE_TO_EXT = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


async def send_payment_approval_request(
    chat_id: str,
    sale_data: dict,
    receipt_bytes: bytes,
    content_type: str,
):
    """
    Send a payment approval request to Telegram Mission Control.

    The receipt file is uploaded as raw bytes directly to Telegram:
    - Images  → sendPhoto   (renders inline in the chat)
    - PDF/other → sendDocument (downloadable file, never expires)

    This avoids presigned URL expiry issues when supervisors check their
    phone hours after the message was sent.
    """
    if not settings.telegram_bot_token:
        logger.warning("telegram_bot_token_not_set")
        return

    phone = sale_data.get("phone", "N/A")
    items = sale_data.get("items", [])
    total_price = sale_data.get("total_price", "0.00")
    checkout_data = sale_data.get("checkout_data", {})
    conversation_id = sale_data.get("conversation_id", "0")

    checkout_str = "\n".join(f"    • {k}: {v}" for k, v in checkout_data.items()) or "    • N/A"
    items_str = "\n".join(
        f"    • {item.get('name', 'Item')} x{item.get('quantity', 1)}" for item in items
    ) or "    • N/A"

    caption = (
        f"🚨 <b>NUEVA SOLICITUD DE APROBACIÓN DE PAGO</b> 🚨\n\n"
        f"📱 <b>Teléfono:</b> {phone}\n"
        f"📋 <b>Items:</b>\n{items_str}\n"
        f"💰 <b>Total a Pagar:</b> {total_price}\n\n"
        f"📝 <b>Datos de Checkout:</b>\n{checkout_str}\n\n"
        f"⚠️ Revisa el comprobante adjunto y decide."
    )

    inline_keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Aprobar", "callback_data": f"approve_sale_{conversation_id}"},
                {"text": "❌ Rechazar", "callback_data": f"reject_sale_{conversation_id}"},
            ]
        ]
    }

    is_image = content_type.startswith("image/")
    endpoint = "sendPhoto" if is_image else "sendDocument"
    field_name = "photo" if is_image else "document"
    ext = _CONTENT_TYPE_TO_EXT.get(content_type, "bin")
    filename = f"comprobante.{ext}"

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{endpoint}"

    form_data = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(inline_keyboard),
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                data=form_data,
                files={field_name: (filename, receipt_bytes, content_type)},
            )
            response.raise_for_status()
            logger.info(
                "telegram_approval_request_sent",
                chat_id=chat_id,
                conversation_id=conversation_id,
                content_type=content_type,
                endpoint=endpoint,
            )
    except httpx.HTTPStatusError as e:
        logger.error("telegram_approval_request_failed", error=str(e), response=e.response.text)
    except Exception as e:
        logger.error("telegram_approval_request_error", error=str(e))
