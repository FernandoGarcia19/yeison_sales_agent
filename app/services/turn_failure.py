"""
When a turn fails (Y-63).

The customer wrote and the pipeline could not answer. Before this, they got silence and nobody knew.
Now the customer gets one short message saying a person will attend them, and the supervisor is told
on WhatsApp and Telegram. At most once per customer every QUIET_SECONDS, so a broken dependency
(OpenAI, the database) does not turn into a stream of apologies. The conversation is not paused:
the agent answers again as soon as it can.
"""

from typing import Any, Optional

import structlog

from app.core import simulation
from app.core.config import settings
from app.core.database import get_session_factory
from app.core.redis_client import acquire_lock
from app.integrations.whatsapp.client import send_whatsapp_message
from app.services.notification_service import NotificationService
from app.services.tenant_service import TenantService

logger = structlog.get_logger()

QUIET_SECONDS = 30 * 60
FALLBACK_TEXT = ("Disculpa, tuve un problema para responderte. "
                 "En un momento te atiende una persona del equipo.")
SUPERVISOR_REASON = "El agente no pudo responder por una falla técnica. Ya le avisó que lo atenderá una persona."


def needs_fallback(result: Any) -> bool:
    """A finished turn that failed and left the customer without an answer."""
    return result is not None and not result.success and not result.response_sent


async def _claim(agent_phone: str, user_phone: str) -> bool:
    try:
        return await acquire_lock(f"turn_failure:{agent_phone}:{user_phone}", ttl=QUIET_SECONDS)
    except Exception as exc:  # Redis down is a likely reason for the failure itself: still tell the customer
        logger.warning("turn_failure_throttle_unavailable", error=str(exc))
        return True


async def _agent_config(agent_phone: str) -> Optional[dict]:
    try:
        async with get_session_factory()() as db:
            found = await TenantService.identify_tenant_by_phone(db, agent_phone)
    except Exception as exc:
        logger.error("turn_failure_agent_lookup_failed", error=str(exc))
        return None
    return (found[2] or {}) if found else None


async def handle_failed_turn(agent_phone: str, user_phone: str, error: Optional[str] = None,
                             message_body: Optional[str] = None, profile_name: Optional[str] = None) -> bool:
    """Tell the customer and the supervisor. Returns True when the customer got the fallback message."""
    if simulation.current() or not agent_phone or not user_phone:
        return False
    config = await _agent_config(agent_phone)
    if config is None:  # not one of our agents (or the database is down): nothing to send from
        logger.error("turn_failure_unhandled", reason="agent_not_found", error=error)
        return False
    if not await _claim(agent_phone, user_phone):
        logger.info("turn_failure_already_reported", customer=user_phone)
        return False

    sent = False
    try:
        await send_whatsapp_message(to=user_phone, body=FALLBACK_TEXT, from_number=agent_phone)
        sent = True
    except Exception as exc:
        logger.error("turn_failure_fallback_not_sent", customer=user_phone, error=str(exc))

    integrations = config.get("integrations") or {}
    notified = False
    if integrations.get("supervisor_number"):
        try:
            notified = await NotificationService.notify_handoff_request(
                supervisor_number=integrations["supervisor_number"], agent_phone=agent_phone,
                customer_phone=user_phone, customer_name=profile_name, reason=SUPERVISOR_REASON,
                conversation_context=(message_body or "")[:500] or None,
            )
        except Exception as exc:
            logger.error("turn_failure_supervisor_whatsapp_failed", error=str(exc))
    telegram = integrations.get("telegram") or {}
    if settings.telegram_bot_token and telegram.get("enabled") and telegram.get("supervisor_chat_id"):
        from app.services import telegram_service
        notified = await telegram_service.send_turn_failure_notification(telegram["supervisor_chat_id"], {
            "phone": user_phone, "name": profile_name, "message": message_body, "fallback_sent": sent,
        }) or notified

    logger.error("turn_failure_reported", customer=user_phone, fallback_sent=sent,
                 supervisor_notified=bool(notified), error=error)
    return sent


async def report(context: Any, result: Any = None, exc: Optional[BaseException] = None) -> bool:
    """Called after every turn: does nothing unless the turn failed without answering. Never raises."""
    if exc is None and not needs_fallback(result):
        return False
    try:
        return await handle_failed_turn(
            agent_phone=context.recipient_phone, user_phone=context.sender_phone,
            error=str(exc) if exc else getattr(result, "error", None),
            message_body=context.message_body, profile_name=context.profile_name,
        )
    except Exception as failure:
        logger.error("turn_failure_handler_failed", error=str(failure))
        return False
