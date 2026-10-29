"""
Dynamic bank QR through the SIP gateway (BMSCYeisonQRGenerator).

Instead of the static QR image the business uploaded, the agent asks the QR
service for a single-use QR with the exact cart total. The customer pays from
any Bolivian bank app and the payment confirms itself: the QR service tells us
(POST /internal/payments/bank-qr), a poller asks it every minute in case that
notice is lost, and a "ya pagué" or a receipt photo also triggers a check.

Whatever the trigger, `confirm_payment` asks the QR service for the charge
status before recording anything, so a forged notice cannot fake a payment.
Amounts come from the stored cart (Decimal); the LLM never decides them.
"""

import base64
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx
import structlog
from sqlalchemy import select, text
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.database import get_session_factory
from app.models import AgentInstance, Lead, SalesConversation, Tenant
from app.models.conversation import ConversationState
from app.services.sale_recorder import build_sale_items, record_sale

logger = structlog.get_logger()

# Currencies SIP charges in (the account's currency must match)
SUPPORTED_CURRENCIES = {"BOB", "USD"}
# SIP's detalleGlosa limit
GLOSA_MAX = 30
# Conversations older than this are no longer polled (QRs expire after SIP_QR_DIAS_VENCIMIENTO, 1 day)
POLL_WINDOW = timedelta(days=2)
CURRENCY_LABELS = {"BOB": "Bs", "USD": "US$"}


class BankQrError(Exception):
    """The QR service refused the request or could not be reached."""


def is_configured() -> bool:
    return bool(settings.bank_qr_api_url and settings.bank_qr_api_key)


def enabled_for(sales_process: Optional[Dict[str, Any]]) -> bool:
    """The agent charges with the bank QR when the business turned it on and the service is configured."""
    return is_configured() and bool((sales_process or {}).get("bank_QR"))


def cart_total(items: List[Dict[str, Any]]) -> Decimal:
    return sum((Decimal(line["line_total"]) for line in build_sale_items(items)), Decimal("0.00"))


def glosa(conversation_id: Optional[int], lead_id: Optional[int]) -> str:
    """Payment description the customer sees in their bank app (SIP allows 30 characters)."""
    return f"Pedido {conversation_id or 0}-{lead_id or 0}"[:GLOSA_MAX]


def amount_label(amount: Any, currency: str) -> str:
    return f"{CURRENCY_LABELS.get(currency, currency)} {Decimal(str(amount)):.2f}"


def pending_charge(cart_contents: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The bank QR this purchase is waiting on, if any."""
    charge = (cart_contents or {}).get("bank_qr")
    return charge if charge and charge.get("status") == "pending" else None


async def _call(method: str, path: str, json: Optional[dict] = None) -> Dict[str, Any]:
    url = f"{settings.bank_qr_api_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=settings.bank_qr_timeout_seconds) as client:
            response = await client.request(method, url, json=json,
                                            headers={"x-api-key": settings.bank_qr_api_key})
    except httpx.HTTPError as e:
        raise BankQrError(f"QR service unreachable: {e}") from e
    if response.status_code >= 400:
        try:
            message = response.json().get("message")
        except ValueError:
            message = response.text[:200]
        raise BankQrError(f"QR service answered {response.status_code}: {message}")
    return response.json()


async def create_charge(tenant_id: int, amount: Decimal, currency: str, description: str) -> Dict[str, Any]:
    """Ask for a single-use QR. Returns alias, idQr, fechaVencimiento and imagenQr (PNG in base64)."""
    return await _call("POST", "/sip/qr", {
        "tenantId": str(tenant_id),
        "monto": float(amount),
        "glosa": description,
        "moneda": currency,
    })


async def get_charge(alias: str) -> Dict[str, Any]:
    return await _call("GET", f"/sip/qr/{alias}")


async def sync_charge(alias: str) -> Dict[str, Any]:
    """Ask SIP for the current status (records the payment on the QR service if SIP says it is paid)."""
    return await _call("POST", f"/sip/qr/{alias}/sincronizar")


async def disable_charge(alias: str) -> None:
    await _call("POST", f"/sip/qr/{alias}/inhabilitar")


def qr_png(charge: Dict[str, Any]) -> bytes:
    return base64.b64decode(charge["imagenQr"])


async def tenant_currency(tenant_id: int) -> str:
    async with get_session_factory()() as db:
        currency = (await db.execute(
            select(Tenant.base_currency).where(Tenant.id == tenant_id)
        )).scalar_one_or_none()
    return (currency or "BOB").upper()


async def check_and_confirm(alias: str, source: str) -> str:
    """Ask SIP for the status (via the QR service) and record the sale if it is paid."""
    try:
        charge = await sync_charge(alias)
    except BankQrError as e:
        logger.warning("bank_qr_sync_failed", alias=alias, source=source, error=str(e))
        return "unavailable"
    if charge.get("estado") != "PAGADO":
        return "not_paid"
    return await confirm_payment(alias, source=source, charge=charge)


async def confirm_payment(alias: str, source: str, charge: Optional[Dict[str, Any]] = None) -> str:
    """
    Record the sale paid with this QR, once. Returns what happened:
    recorded, already_recorded, not_paid, unknown_alias, tenant_mismatch, unavailable.
    """
    if charge is None:
        try:
            charge = await get_charge(alias)
        except BankQrError as e:
            logger.warning("bank_qr_status_failed", alias=alias, source=source, error=str(e))
            return "unavailable"
    if charge.get("estado") != "PAGADO":
        logger.info("bank_qr_not_paid_yet", alias=alias, source=source, estado=charge.get("estado"))
        return "not_paid"

    async with get_session_factory()() as db:
        # Row lock: the notice, the poller and a customer message may arrive at the same time
        conversation = (await db.execute(
            select(SalesConversation)
            .where(text("sales_conversation.cart_contents -> 'bank_qr' ->> 'alias' = :alias"))
            .params(alias=alias)
            .with_for_update()
        )).scalar_one_or_none()
        if not conversation:
            logger.warning("bank_qr_unknown_alias", alias=alias, source=source)
            return "unknown_alias"

        cart = dict(conversation.cart_contents or {})
        pending = cart.get("bank_qr") or {}
        if pending.get("status") == "paid":
            return "already_recorded"

        agent = (await db.execute(
            select(AgentInstance).where(AgentInstance.id == conversation.agent_instance_id)
        )).scalar_one()
        if str(charge.get("tenantId")) != str(agent.tenant_id):
            logger.error("bank_qr_tenant_mismatch", alias=alias, charge_tenant=charge.get("tenantId"),
                         conversation_tenant=agent.tenant_id)
            return "tenant_mismatch"

        # The supervisor may have approved a receipt photo before the bank answered:
        # then the sale exists already and only gets the bank reference, without new messages.
        already_completed = conversation.current_state == ConversationState.ORDER_COMPLETED.value
        sale = None
        try:
            async with db.begin_nested():
                sale = await record_sale(db, conversation, agent, payment_method="qr",
                                         payment_reference=alias)
                if sale and not sale.payment_reference:
                    sale.payment_reference = alias
                    sale.payment_status = "paid"
        except Exception as e:
            logger.error("bank_qr_sale_record_failed", alias=alias, error=str(e))

        lead = (await db.execute(
            select(Lead).where(Lead.conversation_id == conversation.id).order_by(Lead.id.desc()).limit(1)
        )).scalar_one_or_none()
        if lead and lead.status != "converted":
            lead.status = "converted"
            lead.converted_at = datetime.utcnow()

        cart["bank_qr"] = {**pending, "status": "paid", "paid_at": datetime.utcnow().isoformat(),
                           "confirmed_by": source}
        cart["payment_method"] = "qr"
        conversation.cart_contents = cart
        flag_modified(conversation, "cart_contents")
        conversation.current_state = ConversationState.ORDER_COMPLETED.value
        await db.commit()

        customer_phone = conversation.external_user_id
        agent_phone = agent.phone_number
        agent_config = agent.configuration or {}
        conversation_id = conversation.id

    logger.info("bank_qr_payment_confirmed", alias=alias, source=source, conversation_id=conversation_id,
                sale_id=sale.id if sale else None, already_completed=already_completed)
    if already_completed:
        return "already_recorded"
    await _after_payment(sale, customer_phone, agent_phone, agent_config, cart, pending)
    return "recorded"


async def _after_payment(sale, customer_phone: str, agent_phone: str, agent_config: dict,
                         cart: dict, charge: dict) -> None:
    """Stock exit, the customer's confirmation and the supervisor's notice. Failures are logged, never raised."""
    # Imported here: telegram_webhook imports this module's callers
    from app.api.v1.telegram_webhook import _discount_stock
    from app.integrations.whatsapp.client import send_whatsapp_message
    from app.services import telegram_service

    if sale:
        await _discount_stock(sale.tenant_id, sale.id, sale.sale_items)

    amount = amount_label(charge.get("amount", 0), charge.get("currency", "BOB"))
    try:
        await send_whatsapp_message(
            to=customer_phone, from_number=agent_phone,
            body=f"✅ ¡Recibimos tu pago de {amount}! Tu pedido está confirmado y lo estamos preparando. ¡Gracias! 🎉",
        )
    except Exception as e:
        logger.error("bank_qr_customer_notice_failed", error=str(e))

    tg_config = (agent_config.get("integrations") or {}).get("telegram") or {}
    chat_id = tg_config.get("supervisor_chat_id")
    if settings.telegram_bot_token and tg_config.get("enabled") and chat_id:
        await telegram_service.send_bank_qr_paid_notification(chat_id, {
            "phone": customer_phone,
            "items": cart.get("items") or [],
            "total_price": amount,
            "checkout_data": cart.get("checkout_data") or {},
            "invoice_requested": bool(cart.get("invoice_requested")),
            "sale_id": sale.id if sale else None,
        })


async def poll_pending_once() -> int:
    """Check every purchase still waiting on a bank QR. Returns how many were confirmed."""
    since = datetime.utcnow() - POLL_WINDOW
    async with get_session_factory()() as db:
        aliases = (await db.execute(text(
            "select cart_contents -> 'bank_qr' ->> 'alias' from sales_conversation "
            "where current_state = :state and active "
            "and cart_contents -> 'bank_qr' ->> 'status' = 'pending' and last_update >= :since"
        ), {"state": ConversationState.AWAITING_RECEIPT.value, "since": since})).scalars().all()
    confirmed = 0
    for alias in aliases:
        if alias and await check_and_confirm(alias, source="poll") == "recorded":
            confirmed += 1
    return confirmed


async def run_poller() -> None:
    """Background loop started with the app (see main.py)."""
    import asyncio

    logger.info("bank_qr_poller_started", every_seconds=settings.bank_qr_poll_seconds)
    while True:
        await asyncio.sleep(settings.bank_qr_poll_seconds)
        try:
            await poll_pending_once()
        except Exception as e:
            logger.error("bank_qr_poll_failed", error=str(e))
