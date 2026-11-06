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

Every QR sent is a row of `bank_qr_charge`, the source of truth apart from the
conversation: a QR replaced by a newer one is still checked until it expires
(SIP accepts payment of a disabled QR), and so are the QRs of a conversation
paused for a person or of a purchase that was dropped. The sale is recorded from
the cart the QR was sent for, and the follow-ups (stock exit, customer message,
supervisor notice) are an outbox the poller retries until they are done.
"""

import base64
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx
import structlog
from sqlalchemy import select, text, update
from sqlalchemy.orm.attributes import flag_modified

from app.core import simulation
from app.core.config import settings
from app.core.money import money
from app.core.database import get_session_factory
from app.models import AgentInstance, BankQrCharge, Lead, Sale, SalesConversation, Tenant
from app.models.conversation import ConversationState
from app.services.sale_recorder import build_sale_items, record_sale

logger = structlog.get_logger()

# Currencies SIP charges in (the account's currency must match)
SUPPORTED_CURRENCIES = {"BOB", "USD"}
# SIP's detalleGlosa limit
GLOSA_MAX = 30
# Statuses still payable at SIP (a replaced QR was disabled, but SIP accepts its payment anyway)
PAYABLE = ("pending", "replaced")
# Bolivia is UTC-4 all year: local time + 4 h = UTC
BOLIVIA_OFFSET = timedelta(hours=4)
# A QR whose due date is unknown is checked this long (SIP_QR_DIAS_VENCIMIENTO is 1 day)
DEFAULT_LIFETIME = timedelta(days=2)
# Checked this long after its due date, for a bank notice that comes late
EXPIRY_GRACE = timedelta(hours=2)
# Follow-ups (stock exit, messages) are retried this many times, within this window
MAX_FOLLOWUP_ATTEMPTS = 8
FOLLOWUP_WINDOW = timedelta(days=3)
POLL_BATCH = 100
# The part of the cart a QR charges for
SNAPSHOT_KEYS = ("items", "checkout_data", "invoice_requested", "delivery_method")


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
    return money(amount, currency)


def pending_charge(cart_contents: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The bank QR this purchase is waiting on, if any."""
    charge = (cart_contents or {}).get("bank_qr")
    return charge if charge and charge.get("status") == "pending" else None


def _simulated_call(run, method: str, path: str, json: Optional[dict]) -> Dict[str, Any]:
    """The QR service as the test chat sees it: charges live in the run, and only the
    simulator's «Simular pago» marks one paid. Nothing reaches SIP."""
    import uuid

    if method == "POST" and path == "/sip/qr":
        alias = f"sim-{uuid.uuid4().hex[:16]}"
        run.bank_charges[alias] = {"alias": alias, "tenantId": json["tenantId"], "estado": "PENDIENTE",
                                   "monto": json["monto"]}
        expires = (datetime.utcnow() + timedelta(days=1)).strftime("%d/%m/%Y")
        return {"alias": alias, "estado": "PENDIENTE", "idQr": "SIMULADO", "fechaVencimiento": expires,
                "imagenQr": ""}
    alias = path.split("/")[3]
    charge = run.bank_charges.get(alias)
    if charge is None:
        raise BankQrError("QR service answered 404: unknown charge")
    if path.endswith("/inhabilitar"):
        charge["estado"] = "INHABILITADO"
    return dict(charge)


async def _call(method: str, path: str, json: Optional[dict] = None) -> Dict[str, Any]:
    run = simulation.current()
    if run is not None:
        return _simulated_call(run, method, path, json)
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


# --- dates and schedule ----------------------------------------------------------------------

def parse_expiry(value: Any) -> Optional[datetime]:
    """
    SIP's fechaVencimiento ("dd/mm/YYYY", maybe with a time) as naive UTC. A date alone means the end
    of that day in Bolivia (UTC-4). None when it cannot be read.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text_value = str(value).strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(text_value, fmt) + BOLIVIA_OFFSET
        except ValueError:
            pass
    try:
        day = datetime.strptime(text_value[:10], "%d/%m/%Y")
    except ValueError:
        try:
            return datetime.fromisoformat(text_value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return day + timedelta(days=1) + BOLIVIA_OFFSET


def is_expired(charge: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """The QR of the cart can no longer be paid (SIP's due date passed)."""
    expires = parse_expiry((charge or {}).get("expires"))
    return bool(expires and (now or datetime.utcnow()) > expires)


def next_check(created_at: datetime, now: datetime) -> datetime:
    """Every minute while the customer is likely paying, then less often."""
    age = now - created_at
    if age < timedelta(minutes=15):
        step = timedelta(seconds=settings.bank_qr_poll_seconds)
    elif age < timedelta(hours=6):
        step = timedelta(minutes=5)
    else:
        step = timedelta(minutes=15)
    return now + step


def _deadline(row: BankQrCharge) -> datetime:
    """After this a QR is no longer checked: its due date plus a grace for late bank notices."""
    return (row.expires_at or (row.created_at + DEFAULT_LIFETIME)) + EXPIRY_GRACE


def snapshot(cart_contents: Dict[str, Any]) -> Dict[str, Any]:
    """What the QR charges for: the sale is recorded from this even if the cart changes later."""
    return {key: cart_contents[key] for key in SNAPSHOT_KEYS if key in cart_contents}


# --- the charge row --------------------------------------------------------------------------

async def register_charge(*, alias: str, tenant_id: int, agent_instance_id: Optional[int],
                          conversation_id: Optional[int], lead_id: Optional[int], amount: Decimal,
                          currency: str, cart_contents: Dict[str, Any], expires: Any,
                          replaces: Optional[str] = None) -> None:
    """Keep the QR just sent. The one it replaces stays payable at SIP, so it is still checked."""
    now = datetime.utcnow()
    async with get_session_factory()() as db:
        if replaces:
            await db.execute(
                update(BankQrCharge)
                .where(BankQrCharge.alias == replaces, BankQrCharge.status == "pending")
                .values(status="replaced", updated_at=now)
            )
        db.add(BankQrCharge(
            alias=alias, tenant_id=tenant_id, agent_instance_id=agent_instance_id,
            sales_conversation_id=conversation_id, lead_id=lead_id, amount=amount, currency=currency,
            cart=snapshot(cart_contents), status="pending", expires_at=parse_expiry(expires),
            next_check_at=now + timedelta(seconds=settings.bank_qr_poll_seconds), created_at=now, updated_at=now,
        ))
        await db.commit()


async def _adopt(db, conversation: SalesConversation) -> Optional[BankQrCharge]:
    """
    A pending QR known only by its conversation (sent before bank_qr_charge existed, or by the test
    chat, whose rows do not outlive a turn): create its row from the cart.
    """
    cart = conversation.cart_contents or {}
    pending = cart.get("bank_qr") or {}
    if not pending.get("alias"):
        return None
    agent = (await db.execute(
        select(AgentInstance).where(AgentInstance.id == conversation.agent_instance_id)
    )).scalar_one_or_none()
    if not agent:
        return None
    lead_id = (await db.execute(
        select(Lead.id).where(Lead.conversation_id == conversation.id).order_by(Lead.id.desc()).limit(1)
    )).scalar_one_or_none()
    created = pending.get("created_at")
    try:
        created_at = datetime.fromisoformat(created) if created else datetime.utcnow()
    except ValueError:
        created_at = datetime.utcnow()
    row = BankQrCharge(
        alias=pending["alias"], tenant_id=agent.tenant_id, agent_instance_id=agent.id,
        sales_conversation_id=conversation.id, lead_id=lead_id,
        amount=Decimal(str(pending.get("amount") or "0")), currency=pending.get("currency") or "BOB",
        cart=snapshot(cart), status="pending" if pending.get("status") == "pending" else "paid",
        expires_at=parse_expiry(pending.get("expires")), next_check_at=datetime.utcnow(),
        created_at=created_at, updated_at=datetime.utcnow(),
    )
    db.add(row)
    await db.flush()
    logger.info("bank_qr_charge_adopted", alias=row.alias, conversation_id=conversation.id)
    return row


async def _locked_row(db, alias: str) -> Optional[BankQrCharge]:
    row = (await db.execute(
        select(BankQrCharge).where(BankQrCharge.alias == alias).with_for_update()
    )).scalar_one_or_none()
    if row:
        return row
    conversation = (await db.execute(
        select(SalesConversation)
        .where(text("sales_conversation.cart_contents -> 'bank_qr' ->> 'alias' = :alias"))
        .params(alias=alias)
        .with_for_update()
    )).scalar_one_or_none()
    return await _adopt(db, conversation) if conversation else None


# --- confirmation ------------------------------------------------------------------------------

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
    Record the payment of this QR, once. Returns what happened:
    recorded, already_recorded, duplicate, not_paid, unknown_alias, tenant_mismatch, unavailable.

    - The QR the purchase is waiting on: the sale, and the order is complete (a paused conversation
      stays with the person, but resumes completed).
    - An older QR of the same purchase (a newer one replaced it): the sale with the cart that QR was
      for, and the newer QR is disabled.
    - The purchase already has a sale approved from a receipt photo: it only gets the bank reference.
    - It already has a sale paid with another QR: no second sale; the supervisor is told to refund.
    - The purchase was dropped (a new one started): the sale anyway, flagged `late` for the supervisor.
    The stock exit and the messages are follow-ups, retried by the poller until they are done.
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

    to_disable: Optional[str] = None
    async with get_session_factory()() as db:
        # Row lock: the notice, the poller and a customer message may arrive at the same time
        row = await _locked_row(db, alias)
        if not row:
            logger.warning("bank_qr_unknown_alias", alias=alias, source=source)
            return "unknown_alias"
        if row.status == "paid":
            return "already_recorded"
        if str(charge.get("tenantId")) != str(row.tenant_id):
            logger.error("bank_qr_tenant_mismatch", alias=alias, charge_tenant=charge.get("tenantId"),
                         charge_row_tenant=row.tenant_id)
            return "tenant_mismatch"

        conversation = (await db.execute(
            select(SalesConversation).where(SalesConversation.id == row.sales_conversation_id).with_for_update()
        )).scalar_one_or_none()
        agent = (await db.execute(
            select(AgentInstance).where(AgentInstance.id == row.agent_instance_id)
        )).scalar_one_or_none()
        if not conversation or not agent:
            logger.error("bank_qr_charge_orphan", alias=alias)
            return "unknown_alias"

        current_lead = (await db.execute(
            select(Lead).where(Lead.conversation_id == conversation.id).order_by(Lead.id.desc()).limit(1)
        )).scalar_one_or_none()
        lead = current_lead
        if row.lead_id and (not current_lead or current_lead.id != row.lead_id):
            lead = (await db.execute(select(Lead).where(Lead.id == row.lead_id))).scalar_one_or_none() or current_lead
        same_purchase = bool(lead and current_lead and lead.id == current_lead.id)

        cart = dict(conversation.cart_contents or {})
        waiting_on = cart.get("bank_qr") or {}
        is_current = waiting_on.get("alias") == alias

        existing = None
        if lead:
            existing = (await db.execute(
                select(Sale).where(Sale.sales_conversation_id == conversation.id, Sale.lead_id == lead.id,
                                   Sale.active == True)  # noqa: E712
            )).scalar_one_or_none()

        kind, sale, review = "paid", None, None
        if existing and existing.payment_reference and existing.payment_reference != alias:
            kind, review = "duplicate", "duplicate"
        elif existing:
            # The supervisor approved a receipt photo before the bank answered: same sale, now with the reference
            kind, sale = "quiet", existing
            if not existing.payment_reference:
                existing.payment_reference = alias
                existing.payment_status = "paid"
        else:
            if not same_purchase:
                kind, review = "late", "late"
            try:
                async with db.begin_nested():
                    sale = await record_sale(db, conversation, agent, payment_method="qr", payment_reference=alias,
                                             lead=lead, cart=None if is_current else row.cart)
            except Exception as e:
                logger.error("bank_qr_sale_record_failed", alias=alias, error=str(e))
                sale = None

        if lead and kind != "duplicate" and lead.status != "converted":
            lead.status = "converted"
            lead.converted_at = datetime.utcnow()

        if same_purchase and kind in ("paid", "quiet"):
            if not is_current and waiting_on.get("status") == "pending":
                to_disable = waiting_on.get("alias")
            cart["bank_qr"] = {**(waiting_on if is_current else {
                "alias": alias, "amount": str(row.amount), "currency": row.currency,
                "expires": waiting_on.get("expires"), "created_at": row.created_at.isoformat()}),
                "status": "paid", "paid_at": datetime.utcnow().isoformat(), "confirmed_by": source}
            cart["payment_method"] = "qr"
            if conversation.current_state == ConversationState.PAUSED.value:
                # A person has the conversation: it stays with them, and resumes with the order done
                cart["state_before_pause"] = ConversationState.ORDER_COMPLETED.value
            else:
                conversation.current_state = ConversationState.ORDER_COMPLETED.value
            conversation.cart_contents = cart
            flag_modified(conversation, "cart_contents")

        now = datetime.utcnow()
        row.status = "paid"
        row.paid_at = now
        row.confirmed_by = source
        row.review = review
        row.sale_id = sale.id if sale else None
        row.last_checked_at = now
        row.updated_at = now
        todo = {"paid": ["stock", "customer", "supervisor"], "late": ["stock", "customer", "supervisor"],
                "duplicate": ["customer", "supervisor"], "quiet": []}[kind]
        row.followups = {"kind": kind, "todo": todo, "done": {}, "attempts": 0}
        flag_modified(row, "followups")
        if to_disable:
            await db.execute(
                update(BankQrCharge).where(BankQrCharge.alias == to_disable, BankQrCharge.status == "pending")
                .values(status="replaced", updated_at=now)
            )
        row_id = row.id
        await db.commit()

    logger.info("bank_qr_payment_confirmed", alias=alias, source=source, kind=kind,
                sale_id=sale.id if sale else None)
    if to_disable:
        try:
            await disable_charge(to_disable)
        except BankQrError as e:
            logger.warning("bank_qr_disable_failed", alias=to_disable, error=str(e))
    await run_followups(row_id)
    return {"paid": "recorded", "late": "recorded", "duplicate": "duplicate", "quiet": "already_recorded"}[kind]


# --- follow-ups (outbox) -----------------------------------------------------------------------

def _customer_text(kind: str, amount: str) -> str:
    if kind == "late":
        return (f"✅ Recibimos tu pago de {amount} por tu pedido anterior. Ya avisamos al negocio para "
                f"coordinar la entrega. ¡Gracias!")
    if kind == "duplicate":
        return (f"Recibimos un segundo pago de {amount} para un pedido que ya estaba pagado. "
                f"El negocio se comunicará contigo para devolvértelo. 🙏")
    return f"✅ ¡Recibimos tu pago de {amount}! Tu pedido está confirmado y lo estamos preparando. ¡Gracias! 🎉"


_SUPERVISOR = {
    "late": ("PAGO TARDÍO: el cliente pagó un pedido que se había descartado.",
             "👉 Revisa el stock y coordina la entrega con el cliente."),
    "duplicate": ("PAGO DUPLICADO: este pedido ya estaba pagado con otro QR. No se registró otra venta.",
                  "👉 Devuelve este pago al cliente."),
}


async def run_followups(row_id: int) -> bool:
    """
    Stock exit, customer message and supervisor notice of a paid QR, each once: a step is stamped in
    `followups.done` when it works, and the poller retries the rest (up to MAX_FOLLOWUP_ATTEMPTS).
    Returns whether everything is done.
    """
    # Imported here: telegram_webhook imports this module's callers
    from app.api.v1.telegram_webhook import _discount_stock
    from app.integrations.whatsapp.client import send_whatsapp_message
    from app.services import telegram_service

    async with get_session_factory()() as db:
        # skip_locked: if another worker is running these follow-ups, leave them to it
        row = (await db.execute(
            select(BankQrCharge).where(BankQrCharge.id == row_id).with_for_update(skip_locked=True)
        )).scalar_one_or_none()
        if not row or row.status != "paid":
            return False
        followups = dict(row.followups or {})
        done = dict(followups.get("done") or {})
        todo = [step for step in followups.get("todo") or [] if step not in done]
        if not todo:
            return True
        conversation = (await db.execute(
            select(SalesConversation).where(SalesConversation.id == row.sales_conversation_id)
        )).scalar_one_or_none()
        agent = (await db.execute(
            select(AgentInstance).where(AgentInstance.id == row.agent_instance_id)
        )).scalar_one_or_none()
        sale = (await db.execute(select(Sale).where(Sale.id == row.sale_id))).scalar_one_or_none() if row.sale_id else None
        kind = followups.get("kind") or "paid"
        amount = amount_label(row.amount, row.currency)
        cart = row.cart or {}
        customer_phone = conversation.external_user_id if conversation else None
        config = (agent.configuration if agent else None) or {}

        for step in todo:
            ok = False
            try:
                if step == "stock":
                    ok = True if not sale else await _discount_stock(sale.tenant_id, sale.id, sale.sale_items)
                elif step == "customer":
                    if customer_phone and agent and agent.phone_number:
                        await send_whatsapp_message(to=customer_phone, from_number=agent.phone_number,
                                                    body=_customer_text(kind, amount))
                        ok = True
                elif step == "supervisor":
                    tg = (config.get("integrations") or {}).get("telegram") or {}
                    chat_id = tg.get("supervisor_chat_id")
                    if not (settings.telegram_bot_token and tg.get("enabled") and chat_id):
                        ok = True  # nobody to tell
                    else:
                        note, closing = _SUPERVISOR.get(kind, (None, None))
                        ok = await telegram_service.send_bank_qr_paid_notification(chat_id, {
                            "phone": customer_phone, "items": cart.get("items") or [], "total_price": amount,
                            "checkout_data": cart.get("checkout_data") or {},
                            "invoice_requested": bool(cart.get("invoice_requested")),
                            "sale_id": sale.id if sale else None, "note": note, "closing": closing,
                        })
            except Exception as e:
                logger.error("bank_qr_followup_failed", alias=row.alias, step=step, error=str(e))
            if ok:
                done[step] = datetime.utcnow().isoformat()

        followups["done"] = done
        followups["attempts"] = int(followups.get("attempts") or 0) + 1
        pending = [step for step in followups.get("todo") or [] if step not in done]
        if pending and followups["attempts"] >= MAX_FOLLOWUP_ATTEMPTS:
            followups["gave_up"] = datetime.utcnow().isoformat()
            logger.error("bank_qr_followups_gave_up", alias=row.alias, pending=pending)
        row.followups = followups
        flag_modified(row, "followups")
        row.updated_at = datetime.utcnow()
        await db.commit()
    return not pending


# --- poller ------------------------------------------------------------------------------------

async def _adopt_legacy() -> int:
    """Pending QRs that only their conversation knows (sent before bank_qr_charge existed)."""
    async with get_session_factory()() as db:
        conversations = (await db.execute(
            select(SalesConversation).where(
                SalesConversation.active == True,  # noqa: E712
                text("sales_conversation.cart_contents -> 'bank_qr' ->> 'status' = 'pending'"),
                text("not exists (select 1 from bank_qr_charge c "
                     "where c.alias = sales_conversation.cart_contents -> 'bank_qr' ->> 'alias')"),
            ).limit(100)
        )).scalars().all()
        adopted = 0
        for conversation in conversations:
            if await _adopt(db, conversation):
                adopted += 1
        await db.commit()
    return adopted


async def poll_pending_once(now: Optional[datetime] = None) -> int:
    """
    One pass: every QR still payable whose check is due (also replaced ones and those of paused or
    dropped purchases), then the follow-ups still missing. Returns how many payments were confirmed.
    """
    await _adopt_legacy()
    now = now or datetime.utcnow()
    async with get_session_factory()() as db:
        due = (await db.execute(
            select(BankQrCharge.id, BankQrCharge.alias, BankQrCharge.created_at, BankQrCharge.expires_at)
            .where(BankQrCharge.status.in_(PAYABLE),
                   (BankQrCharge.next_check_at == None) | (BankQrCharge.next_check_at <= now))  # noqa: E711
            .order_by(BankQrCharge.next_check_at).limit(POLL_BATCH)
        )).all()
    confirmed = 0
    for row_id, alias, created_at, expires_at in due:
        outcome = await check_and_confirm(alias, source="poll")
        if outcome in ("recorded", "duplicate"):
            confirmed += 1
            continue
        if outcome in ("already_recorded", "tenant_mismatch", "unknown_alias"):
            continue
        probe = BankQrCharge(created_at=created_at, expires_at=expires_at)
        async with get_session_factory()() as db:
            values = {"check_count": BankQrCharge.check_count + 1, "last_checked_at": now, "updated_at": now,
                      "next_check_at": next_check(created_at, now)}
            if now > _deadline(probe):
                values["status"] = "expired"
            await db.execute(update(BankQrCharge).where(BankQrCharge.id == row_id,
                                                        BankQrCharge.status.in_(PAYABLE)).values(**values))
            await db.commit()

    async with get_session_factory()() as db:
        followups = (await db.execute(
            select(BankQrCharge.id).where(
                BankQrCharge.status == "paid",
                BankQrCharge.paid_at >= now - FOLLOWUP_WINDOW,
                text("jsonb_array_length(coalesce(followups -> 'todo', '[]'::jsonb)) > "
                     "(select count(*) from jsonb_object_keys(coalesce(followups -> 'done', '{}'::jsonb)))"),
                text("followups -> 'gave_up' is null"),
            ).limit(POLL_BATCH)
        )).scalars().all()
    for row_id in followups:
        await run_followups(row_id)
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
