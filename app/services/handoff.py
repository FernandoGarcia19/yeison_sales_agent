"""
Handing a conversation to a person (Y-58).

The customer asks for a person (the classifier's handoff_request, or one of the business's
`conversation_settings.handoff_to_human_keywords`): the conversation goes to `paused`, the supervisor
is told on WhatsApp and Telegram (with a "Reanudar agente" button) and the agent stops answering that
customer until someone resumes it or `pause_hours` go by (12 by default). The messages are still kept.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.orm import attributes

from app.core.database import get_session_factory
from app.models import SalesConversation
from app.models.conversation import ConversationState
from app.services.purchase_confirmation import normalize

logger = structlog.get_logger()

PAUSE_HOURS = 12
_DEFAULT_PHRASES = ("hablar con una persona", "hablar con alguien", "hablar con un humano", "persona real",
                    "asesor", "asesora", "operador", "operadora", "agente humano", "atencion humana")


def asks_for_person(message: Optional[str], keywords: Optional[Iterable[str]] = None) -> bool:
    text = f" {normalize(message)} "
    phrases = list(_DEFAULT_PHRASES) + [normalize(k) for k in keywords or [] if normalize(k)]
    return any(f" {phrase} " in text for phrase in phrases)


def pause(cart_contents: Dict[str, Any], previous_state: Optional[str], hours: Optional[float] = None) -> None:
    cart_contents["state_before_pause"] = previous_state or "browsing"
    cart_contents["paused_until"] = (datetime.utcnow() + timedelta(hours=hours or PAUSE_HOURS)).isoformat()


def is_paused(cart_contents: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    until = cart_contents.get("paused_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > (now or datetime.utcnow())
    except ValueError:
        return False


def resume(cart_contents: Dict[str, Any]) -> str:
    """The state the conversation had before the pause."""
    cart_contents.pop("paused_until", None)
    return cart_contents.pop("state_before_pause", None) or "browsing"


async def resume_conversation(conversation_id: int) -> Optional[str]:
    """«Reanudar agente» from Telegram. Returns the customer's phone, or None if it was not paused."""
    session_factory = get_session_factory()
    async with session_factory() as db:
        conversation = (await db.execute(
            select(SalesConversation).where(SalesConversation.id == conversation_id)
        )).scalar_one_or_none()
        if not conversation or conversation.current_state != "paused":
            return None
        cart = dict(conversation.cart_contents or {})
        conversation.current_state = ConversationState(resume(cart))
        conversation.cart_contents = cart
        attributes.flag_modified(conversation, "cart_contents")
        await db.commit()
        logger.info("conversation_resumed", conversation_id=conversation_id)
        return conversation.external_user_id
