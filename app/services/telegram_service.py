import httpx
import structlog
from app.core.config import settings

logger = structlog.get_logger()

async def send_payment_approval_request(chat_id: str, sale_data: dict, receipt_image_url: str):
    """
    Sends a message to the Telegram Mission Control chat asking for payment approval.
    """
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN is not set. Skipping Telegram notification.")
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
    
    # Parse the sale data
    phone = sale_data.get("phone", "N/A")
    items = sale_data.get("items", [])
    total_price = sale_data.get("total_price", "0.00")
    checkout_data = sale_data.get("checkout_data", {})
    conversation_id = sale_data.get("conversation_id", "0")
    
    # Format checkout items into string
    checkout_str = "\n".join([f"    • {k}: {v}" for k, v in checkout_data.items()])
    if not checkout_str:
        checkout_str = "    • N/A"
        
    # Format purchased items
    items_str = "\n".join([f"    • {item.get('name', 'Item')} x{item.get('quantity', 1)}" for item in items])
    if not items_str:
        items_str = "    • N/A"

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
                {"text": "❌ Rechazar", "callback_data": f"reject_sale_{conversation_id}"}
            ]
        ]
    }
    
    payload = {
        "chat_id": chat_id,
        "photo": receipt_image_url,
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": inline_keyboard,
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            logger.info("telegram_approval_request_sent", chat_id=chat_id, conversation_id=conversation_id)
    except httpx.HTTPStatusError as e:
        logger.error("telegram_approval_request_failed", error=str(e), response=e.response.text)
    except Exception as e:
        logger.error("telegram_approval_request_error", error=str(e))
