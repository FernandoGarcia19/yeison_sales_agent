"""
Agent test chat ("Probar agente"): the business talks to its agent before turning it on.

Each turn runs the real pipeline in simulation mode (app/core/simulation.py): one database
transaction that is rolled back, and every outgoing message captured. The conversation
between turns (messages, state, cart, bank QR charges) lives in Redis for a day, so
nothing of the test ever reaches leads, conversations, sales, stock or the metrics.

Actions: message (the customer writes), receipt (sends a payment receipt photo),
pay (the bank confirms a pending bank QR), approve / reject (the supervisor's Telegram
buttons) and reset.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import simulation
from app.core.database import get_engine
from app.core.redis_client import acquire_lock, cache_delete, cache_get, cache_set, release_lock
from app.models import AgentInstance, Lead, SalesConversation

logger = structlog.get_logger()

STATE_TTL = 24 * 3600
CUSTOMER_NAME = "Cliente de prueba"
ACTIONS = {"message", "receipt", "pay", "approve", "reject", "reset"}


class SimulatorError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _key(tenant_id: int, agent_id: int, session_id: str) -> str:
    return f"simulator:{tenant_id}:{agent_id}:{session_id}"


def customer_phone(session_id: str) -> str:
    # external_user_id and lead.phone hold 20 characters; never a real number
    return f"sim{session_id[:16]}"


def _empty_state() -> Dict[str, Any]:
    return {"messages": [], "current_state": "browsing", "cart_contents": {}, "fulfillment_type": None,
            "lead": {"status": "new", "name": CUSTOMER_NAME}, "bank_charges": {}, "transcript": []}


def available_actions(state: Dict[str, Any]) -> List[str]:
    """What the test chat can do next besides writing: the buttons the screen shows."""
    cart = state.get("cart_contents") or {}
    if state.get("current_state") != "awaiting_receipt":
        return []
    if (cart.get("bank_qr") or {}).get("status") == "pending":
        return ["pay", "receipt"]
    if cart.get("payment_method") == "physical" or cart.get("payment_proof_submitted"):
        return ["approve", "reject"]
    return ["receipt"]


async def run_turn(tenant_id: int, agent_id: int, session_id: str, action: str,
                   message: Optional[str] = None, messages: Optional[List[str]] = None) -> Dict[str, Any]:
    if action not in ACTIONS:
        raise SimulatorError(422, f"unknown action {action}")
    key = _key(tenant_id, agent_id, session_id)
    if action == "reset":
        await cache_delete(key)
        return _view(_empty_state(), [], {"action": "reset"})
    # One message or several in a row (answered together, like a WhatsApp batch)
    texts = [m.strip() for m in (messages if messages else [message or ""]) if m and m.strip()]
    if action == "message" and not texts:
        raise SimulatorError(422, "message is empty")
    if any(len(t) > 1000 for t in texts):
        raise SimulatorError(422, "message is too long")

    lock = f"{key}:lock"
    if not await acquire_lock(lock, ttl=120):
        raise SimulatorError(409, "the previous message is still being answered")
    try:
        state = await cache_get(key) or _empty_state()
        if action != "message" and action not in available_actions(state):
            raise SimulatorError(409, f"{action} is not possible now")
        state, events, trace = await _run_in_rollback(tenant_id, agent_id, session_id, state, action, texts)
        await cache_set(key, state, ttl=STATE_TTL)
        return _view(state, events, trace)
    finally:
        await release_lock(lock)


async def _run_in_rollback(tenant_id: int, agent_id: int, session_id: str, state: Dict[str, Any],
                           action: str, texts: List[str]):
    phone = customer_phone(session_id)
    connection = await get_engine().connect()
    transaction = await connection.begin()
    # Every session of the turn commits into savepoints of this one transaction
    factory = async_sessionmaker(bind=connection, class_=AsyncSession, expire_on_commit=False,
                                 autoflush=False, join_transaction_mode="create_savepoint")
    run = simulation.SimulationRun(tenant_id=tenant_id, agent_id=agent_id, session_factory=factory,
                                   customer_phone=phone, bank_charges=dict(state.get("bank_charges") or {}))
    token = simulation.activate(run)
    trace: Dict[str, Any] = {"action": action}
    try:
        async with factory() as db:
            agent = (await db.execute(select(AgentInstance).where(
                AgentInstance.id == agent_id, AgentInstance.tenant_id == tenant_id,
                AgentInstance.active == True,  # noqa: E712
            ))).scalar_one_or_none()
            if not agent:
                raise SimulatorError(404, "agent not found")
            conversation, lead = await _seed(db, agent, phone, state)
            await db.commit()
            conversation_id, agent_phone = conversation.id, agent.phone_number

        if action in ("message", "receipt"):
            trace.update(await _customer_writes(phone, agent_phone or "sim-agent", action, texts))
        elif action == "pay":
            alias = (state["cart_contents"].get("bank_qr") or {}).get("alias")
            run.bank_charges[alias]["estado"] = "PAGADO"
            from app.services import bank_qr
            trace["outcome"] = await bank_qr.confirm_payment(alias, source="simulator")
        else:
            from app.api.v1.telegram_webhook import process_telegram_callback
            await process_telegram_callback(f"{action}_sale_{conversation_id}", chat_id=None, message_id=None)

        async with factory() as db:
            new_state = await _read_back(db, conversation_id, state)
    except SimulatorError:
        raise
    except Exception as e:
        logger.error("simulator_turn_failed", tenant_id=tenant_id, agent_id=agent_id, error=str(e),
                     error_type=type(e).__name__)
        raise SimulatorError(502, "the agent could not answer this message") from e
    finally:
        simulation.deactivate(token)
        await transaction.rollback()
        await connection.close()

    new_state["bank_charges"] = run.bank_charges
    events = _events(run.outbox, phone)
    if action == "message":
        new_state["transcript"].extend({"from": "customer", "text": text, "at": _now()} for text in texts)
    elif action == "receipt":
        new_state["transcript"].append({"from": "customer", "text": "📎 Comprobante de pago (imagen de prueba)",
                                        "at": _now()})
    elif action == "pay":
        new_state["transcript"].append({"from": "system", "text": "🏦 El banco confirmó el pago (simulado)",
                                        "at": _now()})
    else:
        label = "aprobó" if action == "approve" else "rechazó"
        new_state["transcript"].append({"from": "system", "text": f"👤 El supervisor {label} en Telegram (simulado)",
                                        "at": _now()})
    new_state["transcript"].extend(events)
    return new_state, events, trace


async def _seed(db: AsyncSession, agent: AgentInstance, phone: str, state: Dict[str, Any]):
    """The test conversation as it stood after the last turn (it only exists inside this transaction)."""
    conversation = SalesConversation(
        agent_instance_id=agent.id, external_user_id=phone, messages=list(state.get("messages") or []),
        current_state=state.get("current_state") or "browsing",
        cart_contents=dict(state.get("cart_contents") or {}), fulfillment_type=state.get("fulfillment_type"),
    )
    db.add(conversation)
    await db.flush()
    lead_state = state.get("lead") or {}
    lead = Lead(tenant_id=agent.tenant_id, agent_instance_id=agent.id, phone=phone,
                name=lead_state.get("name") or CUSTOMER_NAME, source="simulator",
                status=lead_state.get("status") or "new", conversation_id=conversation.id)
    db.add(lead)
    await db.flush()
    return conversation, lead


async def _customer_writes(phone: str, agent_phone: str, action: str, texts: List[str]) -> Dict[str, Any]:
    from app.services.batch_manager import build_batch_context
    from app.services.pipeline.runner import PipelineRunner

    # Built like a WhatsApp batch, so several messages reach the pipeline exactly as in production
    now = datetime.utcnow().timestamp()
    if action == "receipt":
        queued = [{"message_sid": f"sim-{uuid.uuid4().hex[:12]}", "body": "", "received_at": now,
                   "profile_name": CUSTOMER_NAME, "media_urls": ["simulated://receipt"]}]
    else:
        queued = [{"message_sid": f"sim-{uuid.uuid4().hex[:12]}", "body": text, "received_at": now + i,
                   "profile_name": CUSTOMER_NAME, "media_urls": []} for i, text in enumerate(texts)]
    context = build_batch_context(queued, sender_phone=phone, recipient_phone=agent_phone)
    result = await PipelineRunner().run(context)
    return {"intent": result.intent, "agent_action": result.action_executed, "error": result.error,
            "ms": round(result.processing_time_ms)}


async def _read_back(db: AsyncSession, conversation_id: int, previous: Dict[str, Any]) -> Dict[str, Any]:
    conversation = (await db.execute(
        select(SalesConversation).where(SalesConversation.id == conversation_id)
    )).scalar_one()
    lead = (await db.execute(
        select(Lead).where(Lead.conversation_id == conversation_id).order_by(Lead.id.desc()).limit(1)
    )).scalar_one_or_none()
    return {
        "messages": list(conversation.messages or []),
        "current_state": conversation.current_state,
        "cart_contents": dict(conversation.cart_contents or {}),
        "fulfillment_type": conversation.fulfillment_type,
        "lead": {"status": lead.status if lead else "new", "name": lead.name if lead else CUSTOMER_NAME},
        "transcript": list(previous.get("transcript") or []),
    }


def _events(outbox: List[Dict[str, Any]], phone: str) -> List[Dict[str, Any]]:
    """What the turn would have sent, as the screen shows it: to the customer, the supervisor, the stock."""
    events = []
    for item in outbox:
        if item["channel"] == "whatsapp" and item.get("to") == phone:
            media = item.get("media_url")
            events.append({"from": "agent", "text": item.get("body") or "", "at": _now(),
                           "image": "bank_qr" if media == "simulated://bank-qr" else media})
        elif item["channel"] == "whatsapp":
            events.append({"from": "supervisor_whatsapp", "text": item.get("body") or "", "at": _now()})
        elif item["channel"] == "telegram":
            events.append({"from": "supervisor_telegram", "text": item.get("text") or "",
                           "buttons": item.get("buttons") or [], "at": _now()})
        elif item["channel"] == "inventory":
            names = ", ".join(f"{i.get('name') or 'Producto'} x{i.get('quantity')}" for i in item.get("items") or [])
            events.append({"from": "system", "text": f"📦 Se descontaría del stock: {names}", "at": _now()})
    return events


def _view(state: Dict[str, Any], events: List[Dict[str, Any]], trace: Dict[str, Any]) -> Dict[str, Any]:
    cart = state.get("cart_contents") or {}
    return {
        "transcript": state.get("transcript") or [],
        "events": events,
        "state": state.get("current_state"),
        "cart": [{"name": i.get("name") or i.get("product_name") or "Producto", "quantity": i.get("quantity", 1),
                  "price": i.get("price")} for i in cart.get("items") or []],
        "checkout_data": cart.get("checkout_data") or {},
        "payment": {
            "method": cart.get("payment_method"),
            "bank_qr": cart.get("bank_qr"),
            "receipt_sent": bool(cart.get("payment_proof_submitted")),
            "invoice_requested": bool(cart.get("invoice_requested")),
        },
        "actions": available_actions(state),
        "trace": trace,
    }


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"
