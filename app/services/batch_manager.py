"""
Message batch queue manager for handling multiple rapid messages.

Collects messages within a time window before processing them as a single batch.
"""

import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
import logging

from app.core.redis_client import (
    list_push,
    list_range,
    list_length,
    list_delete,
    acquire_lock,
    release_lock,
    build_batch_queue_key,
    build_batch_lock_key,
)
from app.core.config import settings

logger = logging.getLogger(__name__)


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


# Global registry of active batch timers
_active_timers: Dict[str, asyncio.Task] = {}


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
    
    Args:
        agent_phone: Agent's phone number (recipient_phone)
        user_phone: User's phone number (sender_phone)
        message_sid: Unique Twilio message ID
        body: Message text content
        profile_name: WhatsApp profile name
        media_urls: List of media URLs
    
    Returns:
        True if message was enqueued successfully
    """
    if not settings.batch_enabled:
        return False
    
    queue_key = build_batch_queue_key(agent_phone, user_phone)
    lock_key = build_batch_lock_key(agent_phone, user_phone)
    
    # Check for duplicate message_sid in queue
    existing_messages = await list_range(queue_key)
    for msg_data in existing_messages:
        if msg_data.get("message_sid") == message_sid:
            logger.warning(f"Duplicate message {message_sid} ignored")
            return False
    
    # Create batch message
    batch_msg = BatchMessage(
        message_sid=message_sid,
        body=body,
        received_at=datetime.utcnow().timestamp(),
        profile_name=profile_name,
        media_urls=media_urls,
    )
    
    # Add to queue
    queue_length = await list_push(queue_key, batch_msg.to_dict())
    logger.info(f"Message {message_sid} added to queue {queue_key} (length: {queue_length})")
    
    # Check if we should fire early due to max batch size
    if queue_length >= settings.batch_max_messages:
        logger.info(f"Batch size limit reached ({queue_length}), processing immediately")
        # Cancel existing timer if any
        timer_id = f"{agent_phone}:{user_phone}"
        if timer_id in _active_timers:
            _active_timers[timer_id].cancel()
            del _active_timers[timer_id]
        # Process immediately
        asyncio.create_task(_process_batch(agent_phone, user_phone))
        return True
    
    # Start batch timer if not already running
    if not await acquire_lock(lock_key, ttl=settings.batch_window_seconds + 5):
        logger.debug(f"Batch timer already running for {queue_key}")
        return True
    
    # Start new timer
    timer_id = f"{agent_phone}:{user_phone}"
    if timer_id in _active_timers:
        # Cancel old timer
        _active_timers[timer_id].cancel()
    
    # Create new timer task
    timer_task = asyncio.create_task(
        _batch_timer(agent_phone, user_phone)
    )
    _active_timers[timer_id] = timer_task
    
    logger.info(f"Started batch timer for {queue_key} ({settings.batch_window_seconds}s)")
    
    return True


async def _batch_timer(agent_phone: str, user_phone: str):
    """
    Timer that waits for the batch window then processes the batch.
    
    Args:
        agent_phone: Agent's phone number
        user_phone: User's phone number
    """
    timer_id = f"{agent_phone}:{user_phone}"
    
    try:
        # Wait for batch window
        await asyncio.sleep(settings.batch_window_seconds)
        
        # Process the batch
        await _process_batch(agent_phone, user_phone)
        
    except asyncio.CancelledError:
        logger.info(f"Batch timer cancelled for {timer_id}")
    except Exception as e:
        logger.error(f"Error in batch timer for {timer_id}: {e}", exc_info=True)
    finally:
        # Clean up
        lock_key = build_batch_lock_key(agent_phone, user_phone)
        await release_lock(lock_key)
        
        if timer_id in _active_timers:
            del _active_timers[timer_id]


async def _process_batch(agent_phone: str, user_phone: str):
    """
    Process all messages in the batch queue.
    
    Args:
        agent_phone: Agent's phone number
        user_phone: User's phone number
    """
    from app.services.pipeline.runner import PipelineRunner
    from app.schemas.pipeline import PipelineContext
    
    queue_key = build_batch_queue_key(agent_phone, user_phone)
    
    # Get all messages from queue
    messages_data = await list_range(queue_key)
    
    if not messages_data:
        logger.warning(f"No messages in queue {queue_key} when processing")
        return
    
    # Convert to BatchMessage objects and sort by received_at
    batch_messages = [BatchMessage.from_dict(msg) for msg in messages_data]
    batch_messages.sort(key=lambda m: m.received_at)
    
    logger.info(f"Processing batch of {len(batch_messages)} messages from {user_phone}")
    
    # Delete the queue
    await list_delete(queue_key)
    
    # Use the first message as the primary context, add others as batch
    first_msg = batch_messages[0]
    
    # Create pipeline context with batched messages
    context = PipelineContext(
        message_sid=first_msg.message_sid,
        sender_phone=user_phone,
        recipient_phone=agent_phone,
        message_body=first_msg.body,
        profile_name=first_msg.profile_name,
        media_urls=first_msg.media_urls,
        is_batch=len(batch_messages) > 1,
        batch_messages=[msg.to_dict() for msg in batch_messages] if len(batch_messages) > 1 else None,
    )
    
    # Run pipeline
    try:
        runner = PipelineRunner()
        result = await runner.run(context)
        
        if result.success:
            logger.info(f"Batch processed successfully: {result.message}")
        else:
            logger.error(f"Batch processing failed: {result.message}")
    
    except Exception as e:
        logger.error(f"Error processing batch: {e}", exc_info=True)


async def get_queue_status(agent_phone: str, user_phone: str) -> Dict[str, Any]:
    """
    Get current status of a batch queue.
    
    Args:
        agent_phone: Agent's phone number
        user_phone: User's phone number
    
    Returns:
        Dictionary with queue status
    """
    queue_key = build_batch_queue_key(agent_phone, user_phone)
    lock_key = build_batch_lock_key(agent_phone, user_phone)
    timer_id = f"{agent_phone}:{user_phone}"
    
    queue_length = await list_length(queue_key)
    has_timer = timer_id in _active_timers
    
    return {
        "queue_key": queue_key,
        "queue_length": queue_length,
        "timer_active": has_timer,
        "batch_enabled": settings.batch_enabled,
        "batch_window_seconds": settings.batch_window_seconds,
    }
