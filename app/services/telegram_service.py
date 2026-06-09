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
    def _fmt_item(item: dict) -> str:
        name = item.get("name", "Item")
        qty = int(item.get("quantity", 1))
        unit_price = float(item.get("price", 0))
        subtotal = unit_price * qty
        if unit_price:
            return f"    • {name} x{qty} @ ${unit_price:.2f} = <b>${subtotal:.2f}</b>"
        return f"    • {name} x{qty}"

    items_str = "\n".join(_fmt_item(item) for item in items) or "    • N/A"

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


async def send_purchase_intent_notification(
    chat_id: str,
    sale_data: dict,
):
    """
    Notify the supervisor of a physical-payment purchase intent.

    No receipt file is attached (the customer pays in person).
    Approve/reject buttons are included so the supervisor can confirm
    the order, which triggers a WhatsApp confirmation to the customer.
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

    def _fmt_item(item: dict) -> str:
        name = item.get("name", "Item")
        qty = int(item.get("quantity", 1))
        unit_price = float(item.get("price", 0))
        subtotal = unit_price * qty
        if unit_price:
            return f"    • {name} x{qty} @ ${unit_price:.2f} = <b>${subtotal:.2f}</b>"
        return f"    • {name} x{qty}"

    items_str = "\n".join(_fmt_item(item) for item in items) or "    • N/A"

    text = (
        f"🛒 <b>NUEVA INTENCIÓN DE COMPRA — PAGO FÍSICO</b>\n\n"
        f"📱 <b>Teléfono:</b> {phone}\n"
        f"📋 <b>Items:</b>\n{items_str}\n"
        f"💰 <b>Total estimado:</b> {total_price}\n\n"
        f"📝 <b>Datos de entrega:</b>\n{checkout_str}\n\n"
        f"ℹ️ El cliente eligió pago físico/efectivo. Aprueba para confirmar el pedido o rechaza para cancelarlo."
    )

    inline_keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Aprobar pedido", "callback_data": f"approve_sale_{conversation_id}"},
                {"text": "❌ Rechazar pedido", "callback_data": f"reject_sale_{conversation_id}"},
            ]
        ]
    }

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(inline_keyboard),
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            logger.info(
                "telegram_purchase_intent_sent",
                chat_id=chat_id,
                conversation_id=conversation_id,
            )
    except httpx.HTTPStatusError as e:
        logger.error("telegram_purchase_intent_failed", error=str(e), response=e.response.text)
    except Exception as e:
        logger.error("telegram_purchase_intent_error", error=str(e))
