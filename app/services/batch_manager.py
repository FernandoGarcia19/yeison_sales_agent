"""
Message batching: a customer often writes in pieces ("hola" / "quiero 2 medias" / a photo of the
receipt). Messages are queued in Redis and answered together once the customer stops writing.

Y-57:
- Sliding window: each message moves the answer to `batch_window_seconds` after it, never later than
  `batch_max_wait_seconds` after the first one; `batch_max_messages` answers at once.
- The queue is read and deleted in one transaction: a message that arrives meanwhile is not lost.
- One batch of a customer at a time (`batch_run:*` lock): what arrives while the agent answers
  forms the next batch, which waits for the previous one to finish.
- The whole batch reaches the pipeline (joined text, every photo), so the classifier sees it all.
- Customers with queued messages are kept in `batch_pending`; a restarted container answers them.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.redis_client import (
    BATCH_PENDING_KEY,
    acquire_token_lock,
    build_batch_deadline_key,
    build_batch_first_key,
    build_batch_lock_key,
    build_batch_queue_key,
    build_batch_run_key,
    get_redis_client,
    is_msg_duplicate,
    list_length,
    list_push,
    release_token_lock,
    set_msg_dedup,
)

logger = logging.getLogger(__name__)

# The run lock is checked this often while a batch waits for the previous one
_RUN_POLL_SECONDS = 0.25


class BatchMessage:
    """Represents a single message in the batch queue."""

    def __init__(
        self,
        message_sid: str,
        body: str,
        received_at: float,
        profile_name: Optional[str] = None,
        media_urls: list = None
    ):
        self.message_sid = message_sid
        self.body = body
        self.received_at = received_at
        self.profile_name = profile_name
        self.media_urls = media_urls or []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for Redis storage."""
        return {
            "message_sid": self.message_sid,
            "body": self.body,
            "received_at": self.received_at,
            "profile_name": self.profile_name,
            "media_urls": self.media_urls,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BatchMessage":
        """Create from dictionary loaded from Redis."""
        return cls(
            message_sid=data["message_sid"],
            body=data["body"],
            received_at=data["received_at"],
            profile_name=data.get("profile_name"),
            media_urls=data.get("media_urls", []),
        )


# Batch timers of this process, by customer
_active_timers: Dict[str, asyncio.Task] = {}
# Strong references for background tasks to prevent them from being garbage collected
_background_tasks = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _pending_member(agent_phone: str, user_phone: str) -> str:
    return json.dumps([agent_phone, user_phone])


def build_batch_context(messages: List[Dict[str, Any]], sender_phone: str, recipient_phone: str):
    """The pipeline context of a batch: the texts joined in order and the photos of every message."""
    from app.schemas.pipeline import PipelineContext

    batch = sorted((BatchMessage.from_dict(m) for m in messages), key=lambda m: m.received_at)
    first = batch[0]
    return PipelineContext(
        message_sid=first.message_sid,
        sender_phone=sender_phone,
        recipient_phone=recipient_phone,
        message_body="\n".join(m.body for m in batch if (m.body or "").strip()),
        profile_name=next((m.profile_name for m in batch if m.profile_name), None),
        media_urls=[url for m in batch for url in m.media_urls],
        is_batch=len(batch) > 1,
        batch_messages=[m.to_dict() for m in batch] if len(batch) > 1 else None,
    )


async def enqueue_message(
    agent_phone: str,
    user_phone: str,
    message_sid: str,
    body: str,
    profile_name: Optional[str] = None,
    media_urls: list = None,
) -> bool:
    """
    Enqueue a message to the batch queue.

    Returns:
        True if message was enqueued successfully
    """
    if not settings.batch_enabled:
        return False

    # Primary dedup guard: persists beyond the queue lifetime, catches Twilio retries
    # that arrive after the batch queue has already been deleted and processed.
    if await is_msg_duplicate(message_sid):
        logger.warning(f"Duplicate message {message_sid} ignored (dedup key hit)")
        return False

    # Mark as accepted immediately so concurrent retries are also rejected.
    await set_msg_dedup(message_sid, ttl=300)

    queue_key = build_batch_queue_key(agent_phone, user_phone)
    now = time.time()
    batch_msg = BatchMessage(
        message_sid=message_sid,
        body=body,
        received_at=datetime.utcnow().timestamp(),
        profile_name=profile_name,
        media_urls=media_urls,
    )

    redis = get_redis_client()
    expiry = int(settings.batch_max_wait_seconds + settings.batch_run_timeout_seconds + 60)
    await redis.sadd(BATCH_PENDING_KEY, _pending_member(agent_phone, user_phone))
    queue_length = await list_push(queue_key, batch_msg.to_dict())
    # Each message moves the answer; the first one fixes the latest possible moment
    await redis.set(build_batch_deadline_key(agent_phone, user_phone), now + settings.batch_window_seconds, ex=expiry)
    await redis.set(build_batch_first_key(agent_phone, user_phone), now, nx=True, ex=expiry)
    logger.info(f"Message {message_sid} added to queue {queue_key} (length: {queue_length})")

    timer_id = f"{agent_phone}:{user_phone}"
    if queue_length >= settings.batch_max_messages:
        logger.info(f"Batch size limit reached ({queue_length}), processing immediately")
        timer = _active_timers.pop(timer_id, None)
        if timer:
            timer.cancel()
        _spawn(_process_batch(agent_phone, user_phone))
        return True

    # One timer per customer; the running one sees the new deadline
    token = uuid.uuid4().hex
    lock_ttl = int(settings.batch_max_wait_seconds + 10)
    if not await acquire_token_lock(build_batch_lock_key(agent_phone, user_phone), token, lock_ttl):
        logger.debug(f"Batch timer already running for {queue_key}")
        return True

    _active_timers[timer_id] = _spawn(_batch_timer(agent_phone, user_phone, token))
    logger.info(f"Started batch timer for {queue_key} ({settings.batch_window_seconds}s)")
    return True


async def _batch_timer(agent_phone: str, user_phone: str, token: str):
    """Wait until the customer stops writing (or the maximum wait), then answer the batch."""
    timer_id = f"{agent_phone}:{user_phone}"
    lock_key = build_batch_lock_key(agent_phone, user_phone)
    redis = get_redis_client()
    try:
        while True:
            deadline = await redis.get(build_batch_deadline_key(agent_phone, user_phone))
            first = await redis.get(build_batch_first_key(agent_phone, user_phone))
            now = time.time()
            wake = min(float(deadline or now), float(first or now) + settings.batch_max_wait_seconds)
            if wake <= now:
                break
            await asyncio.sleep(wake - now)
    except asyncio.CancelledError:
        logger.info(f"Batch timer cancelled for {timer_id}")
        await release_token_lock(lock_key, token)
        raise
    finally:
        if _active_timers.get(timer_id) is asyncio.current_task():
            del _active_timers[timer_id]

    # Free the timer before answering: what the customer writes meanwhile starts the next batch
    await release_token_lock(lock_key, token)
    try:
        await _process_batch(agent_phone, user_phone)
    except Exception as e:
        logger.error(f"Error in batch timer for {timer_id}: {e}", exc_info=True)


async def take_batch(agent_phone: str, user_phone: str) -> List[Dict[str, Any]]:
    """Read and empty the queue in one transaction, and reset the window for the next batch."""
    redis = get_redis_client()
    async with redis.pipeline(transaction=True) as pipe:
        pipe.lrange(build_batch_queue_key(agent_phone, user_phone), 0, -1)
        pipe.delete(build_batch_queue_key(agent_phone, user_phone))
        pipe.delete(build_batch_first_key(agent_phone, user_phone))
        raw, *_ = await pipe.execute()
    return [json.loads(item) for item in raw or []]


async def _acquire_run_lock(agent_phone: str, user_phone: str, token: str) -> bool:
    """Wait for the previous batch of the same customer; give up after batch_run_timeout_seconds."""
    key = build_batch_run_key(agent_phone, user_phone)
    ttl = int(settings.batch_run_timeout_seconds)
    deadline = time.monotonic() + ttl
    while not await acquire_token_lock(key, token, ttl):
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(_RUN_POLL_SECONDS)
    return True


async def _process_batch(agent_phone: str, user_phone: str):
    """Answer everything queued for this customer, one batch at a time."""
    from app.services.pipeline.runner import PipelineRunner

    token = uuid.uuid4().hex
    run_key = build_batch_run_key(agent_phone, user_phone)
    if not await _acquire_run_lock(agent_phone, user_phone, token):
        # The previous answer is stuck; answering anyway is better than losing the message
        logger.error(f"Batch of {user_phone} waited too long for the previous one; answering anyway")
    try:
        messages = await take_batch(agent_phone, user_phone)
        if not messages:
            return
        logger.info(f"Processing batch of {len(messages)} messages from {user_phone}")
        context = build_batch_context(messages, user_phone, agent_phone)
        try:
            result = await PipelineRunner().run(context)
            if result.success:
                logger.info(
                    f"Batch processed successfully (message_sid={result.message_sid}, intent={result.intent}, "
                    f"action={result.action_executed}, response_sent={result.response_sent})"
                )
            else:
                logger.error(f"Batch processing failed (message_sid={result.message_sid}, error={result.error})")
        except Exception as e:
            logger.error(f"Error processing batch: {e}", exc_info=True)
    finally:
        await release_token_lock(run_key, token)
        if not await list_length(build_batch_queue_key(agent_phone, user_phone)):
            await get_redis_client().srem(BATCH_PENDING_KEY, _pending_member(agent_phone, user_phone))


async def recover_pending() -> int:
    """At startup: answer the customers whose messages were queued when the container stopped."""
    redis = get_redis_client()
    recovered = 0
    for member in await redis.smembers(BATCH_PENDING_KEY):
        try:
            agent_phone, user_phone = json.loads(member)
        except (ValueError, TypeError):
            await redis.srem(BATCH_PENDING_KEY, member)
            continue
        if await list_length(build_batch_queue_key(agent_phone, user_phone)):
            _spawn(_process_batch(agent_phone, user_phone))
            recovered += 1
        else:
            await redis.srem(BATCH_PENDING_KEY, member)
    if recovered:
        logger.info(f"Recovered {recovered} queued batches")
    return recovered


async def get_queue_status(agent_phone: str, user_phone: str) -> Dict[str, Any]:
    """Current status of a customer's batch queue."""
    queue_key = build_batch_queue_key(agent_phone, user_phone)
    timer_id = f"{agent_phone}:{user_phone}"
    return {
        "queue_key": queue_key,
        "queue_length": await list_length(queue_key),
        "timer_active": timer_id in _active_timers,
        "batch_enabled": settings.batch_enabled,
        "batch_window_seconds": settings.batch_window_seconds,
    }
