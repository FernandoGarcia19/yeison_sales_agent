"""
A person attends a WhatsApp conversation from the panel (Y-69): pause the agent, answer as the business and
give the conversation back. The panel calls the internal routes (app/api/v1/internal_conversations.py) after
checking the conversation is of the business; this checks it again with the tenant it sends.

The pause is the handoff's (Y-58): `state_before_pause` and `paused_until` in `cart_contents`, and the agent
stays quiet with this customer until someone resumes it or the pause ends (the agent's `pause_hours`, 12 by
default). A person's message is stored with role "assistant", because it is the business speaking and the agent
reads it that way when it is resumed, and with "sender": "human" so the panel tells it apart.
"""

from typing import Any, Dict, Optional, Tuple

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from app.core.database import get_session_factory
from app.integrations.whatsapp.client import send_whatsapp_message
from app.integrations.whatsapp.formatting import to_whatsapp
from app.models import AgentInstance, SalesConversation
from app.models.conversation import ConversationState
from app.services import handoff

logger = structlog.get_logger()

PAUSED = ConversationState.PAUSED.value


class InboxError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


async def _load(db: AsyncSession, conversation_id: int, tenant_id: int) -> Tuple[SalesConversation, AgentInstance]:
    """The conversation, locked, only if one of this business's agents has it."""
    row = (await db.execute(
        select(SalesConversation, AgentInstance)
        .join(AgentInstance, AgentInstance.id == SalesConversation.agent_instance_id)
        .where(SalesConversation.id == conversation_id, AgentInstance.tenant_id == tenant_id)
        .with_for_update(of=SalesConversation)
    )).one_or_none()
    if row is None:
        raise InboxError(404, "conversation not found")
    return row[0], row[1]


def _state(conversation: SalesConversation) -> str:
    return getattr(conversation.current_state, "value", conversation.current_state) or "browsing"


def _pause_hours(agent: AgentInstance) -> Optional[float]:
    settings_ = ((agent.configuration or {}).get("conversation_settings") or {})
    hours = settings_.get("pause_hours")
    return hours if isinstance(hours, (int, float)) and hours > 0 else None


def _take_over(conversation: SalesConversation, agent: AgentInstance) -> None:
    """Pause the agent; if it already was, the pause starts again and the state to go back to is kept."""
    cart = dict(conversation.cart_contents or {})
    if _state(conversation) == PAUSED:
        handoff.pause(cart, cart.get("state_before_pause"), _pause_hours(agent))
    else:
        handoff.pause(cart, _state(conversation), _pause_hours(agent))
        conversation.current_state = ConversationState.PAUSED
    conversation.cart_contents = cart
    attributes.flag_modified(conversation, "cart_contents")


def view(conversation: SalesConversation) -> Dict[str, Any]:
    cart = conversation.cart_contents or {}
    state = _state(conversation)
    return {
        "id": conversation.id,
        "state": state,
        "state_before_pause": cart.get("state_before_pause") if state == PAUSED else None,
        "paused_until": cart.get("paused_until") if state == PAUSED else None,
    }


async def _forget_history(tenant_id: int, conversation_id: int) -> None:
    """The pipeline caches the history for 5 minutes; the next turn must see the person's message."""
    from app.core.redis_client import build_conversation_cache_key, cache_delete

    try:
        await cache_delete(build_conversation_cache_key(tenant_id, conversation_id))
    except Exception as e:  # Redis down: the cache expires on its own
        logger.warning("inbox_cache_delete_failed", conversation_id=conversation_id, error=str(e))


async def pause_conversation(conversation_id: int, tenant_id: int) -> Dict[str, Any]:
    async with get_session_factory()() as db:
        conversation, agent = await _load(db, conversation_id, tenant_id)
        _take_over(conversation, agent)
        await db.commit()
        logger.info("conversation_paused_from_panel", conversation_id=conversation_id, tenant_id=tenant_id)
        return view(conversation)


async def resume_conversation(conversation_id: int, tenant_id: int) -> Dict[str, Any]:
    """Back to the state it had before the pause; nothing changes if it was not paused."""
    async with get_session_factory()() as db:
        conversation, _ = await _load(db, conversation_id, tenant_id)
        if _state(conversation) == PAUSED:
            cart = dict(conversation.cart_contents or {})
            conversation.current_state = ConversationState(handoff.resume(cart))
            conversation.cart_contents = cart
            attributes.flag_modified(conversation, "cart_contents")
            await db.commit()
            logger.info("conversation_resumed_from_panel", conversation_id=conversation_id, tenant_id=tenant_id)
        return view(conversation)


async def reply(conversation_id: int, tenant_id: int, text: str, author: Optional[str] = None) -> Dict[str, Any]:
    """Send the person's text to the customer, keep it in the conversation and leave the agent paused."""
    body = to_whatsapp((text or "").strip())
    if not body:
        raise InboxError(422, "empty message")
    async with get_session_factory()() as db:
        conversation, agent = await _load(db, conversation_id, tenant_id)
        if not conversation.external_user_id or not agent.phone_number:
            raise InboxError(409, "this conversation has no WhatsApp number to answer")
        try:
            message_sid = await send_whatsapp_message(to=conversation.external_user_id, body=body,
                                                      from_number=agent.phone_number)
        except Exception as e:
            logger.error("inbox_reply_send_failed", conversation_id=conversation_id, error=str(e))
            raise InboxError(502, "WhatsApp did not take the message")
        extra: Dict[str, Any] = {"sender": "human", "action_type": "human_reply", "message_sid": message_sid}
        if author:
            extra["author"] = author
        conversation.add_message(role="assistant", content=body, **extra)
        attributes.flag_modified(conversation, "messages")
        _take_over(conversation, agent)
        await db.commit()
        logger.info("conversation_human_reply", conversation_id=conversation_id, tenant_id=tenant_id)
        result = view(conversation)
    await _forget_history(tenant_id, conversation_id)
    return {**result, "message": conversation.get_last_message()}
