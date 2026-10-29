"""
Message batching against a real Redis (Y-57). Skipped without TEST_REDIS_URL (the CI has one).

What it fixes: the classifier saw only the first message of a batch, a photo in a later message was
lost, the window did not move with each message, the queue was read and deleted in two steps, and
two batches of the same customer could run at once.
"""

import asyncio
import os
import time
import uuid

import pytest

from app.core import redis_client
from app.core.config import settings
from app.services import batch_manager

REDIS_URL = os.environ.get("TEST_REDIS_URL")


def test_batch_context_joins_texts_and_keeps_every_photo():
    messages = [
        {"message_sid": "b", "body": "aquí va mi pago", "received_at": 2.0, "profile_name": "Ana", "media_urls": []},
        {"message_sid": "a", "body": "Hola", "received_at": 1.0, "profile_name": None, "media_urls": []},
        {"message_sid": "c", "body": "", "received_at": 3.0, "profile_name": None, "media_urls": ["https://x/receipt.jpg"]},
    ]
    context = batch_manager.build_batch_context(messages, sender_phone="+591700", recipient_phone="+591800")
    assert context.message_body == "Hola\naquí va mi pago"
    assert context.media_urls == ["https://x/receipt.jpg"]
    assert context.message_sid == "a" and context.profile_name == "Ana"
    assert context.is_batch and [m["message_sid"] for m in context.batch_messages] == ["a", "b", "c"]

    single = batch_manager.build_batch_context(messages[:1], sender_phone="+591700", recipient_phone="+591800")
    assert single.message_body == "aquí va mi pago" and not single.is_batch and single.batch_messages is None


class Runs:
    """Stands in for the pipeline: records each batch and can take its time."""

    def __init__(self):
        self.batches = []
        self.active = 0
        self.overlapped = False
        self.delay = 0.0

    def runner(self):
        runs = self

        class FakeRunner:
            async def run(self, context):
                runs.active += 1
                runs.overlapped |= runs.active > 1
                runs.batches.append({"body": context.message_body, "media": context.media_urls,
                                     "started": time.monotonic()})
                await asyncio.sleep(runs.delay)
                runs.active -= 1

                class Result:
                    success, message_sid, intent, action_executed, response_sent, error = True, "x", None, None, True, None
                return Result()

        return FakeRunner


@pytest.fixture
async def redis_env(monkeypatch):
    if not REDIS_URL:
        pytest.skip("TEST_REDIS_URL not set")
    from app.services.pipeline import runner as runner_module

    monkeypatch.setattr(settings, "redis_url", REDIS_URL)
    monkeypatch.setattr(settings, "batch_enabled", True)
    monkeypatch.setattr(settings, "batch_window_seconds", 0.4)
    monkeypatch.setattr(settings, "batch_max_wait_seconds", 2.0)
    monkeypatch.setattr(settings, "batch_max_messages", 5)
    monkeypatch.setattr(settings, "batch_run_timeout_seconds", 10)
    redis_client._redis_client = None
    redis_client._connection_pool = None
    runs = Runs()
    monkeypatch.setattr(runner_module, "PipelineRunner", runs.runner())
    phones = (f"+5918{uuid.uuid4().hex[:8]}", f"+5917{uuid.uuid4().hex[:8]}")
    yield runs, phones
    for task in list(batch_manager._background_tasks):
        task.cancel()
    await asyncio.sleep(0)
    await redis_client.close_redis_connection()
    redis_client._redis_client = None
    redis_client._connection_pool = None


async def _send(phones, body, media=None):
    await batch_manager.enqueue_message(phones[0], phones[1], f"m-{uuid.uuid4().hex}", body, media_urls=media)


async def _wait_for(condition, timeout=6.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_the_window_moves_with_each_message(redis_env):
    runs, phones = redis_env
    started = time.monotonic()
    for body in ("Hola", "quiero 2 packs", "de medias"):
        await _send(phones, body)
        await asyncio.sleep(0.25)  # less than the window: the customer is still writing
    assert await _wait_for(lambda: runs.batches)
    await asyncio.sleep(0.6)
    assert [b["body"] for b in runs.batches] == ["Hola\nquiero 2 packs\nde medias"]
    # Answered 0.4 s after the last message (sent at 0.5 s), not 0.4 s after the first
    assert runs.batches[0]["started"] - started >= 0.8


async def test_a_photo_in_a_later_message_reaches_the_pipeline(redis_env):
    runs, phones = redis_env
    await _send(phones, "aquí va mi pago")
    await _send(phones, "", media=["https://x/receipt.jpg"])
    assert await _wait_for(lambda: runs.batches)
    assert runs.batches[0]["media"] == ["https://x/receipt.jpg"]


async def test_the_window_never_stretches_past_the_maximum(redis_env):
    runs, phones = redis_env
    started = time.monotonic()
    for i in range(12):  # 3 s of messages every 0.25 s, maximum wait 2 s
        await _send(phones, f"m{i}")
        await asyncio.sleep(0.25)
    assert await _wait_for(lambda: sum(b["body"].count("m") for b in runs.batches) == 12)
    assert runs.batches[0]["started"] - started <= 2.5
    assert len(runs.batches) >= 2


async def test_the_maximum_number_of_messages_answers_at_once(redis_env, monkeypatch):
    runs, phones = redis_env
    monkeypatch.setattr(settings, "batch_window_seconds", 5)
    monkeypatch.setattr(settings, "batch_max_wait_seconds", 10)
    monkeypatch.setattr(settings, "batch_max_messages", 3)
    started = time.monotonic()
    for body in ("a", "b", "c"):
        await _send(phones, body)
    assert await _wait_for(lambda: runs.batches, timeout=2)
    assert runs.batches[0]["body"] == "a\nb\nc" and runs.batches[0]["started"] - started < 1.5


async def test_one_batch_of_a_customer_at_a_time(redis_env):
    runs, phones = redis_env
    runs.delay = 1.0  # the model takes its time
    await _send(phones, "primero")
    assert await _wait_for(lambda: runs.batches)
    await _send(phones, "segundo")  # arrives while the agent is answering
    await _send(phones, "tercero")
    assert await _wait_for(lambda: len(runs.batches) == 2)
    assert not runs.overlapped
    assert [b["body"] for b in runs.batches] == ["primero", "segundo\ntercero"]
    assert runs.batches[1]["started"] - runs.batches[0]["started"] >= 1.0


async def test_take_batch_empties_the_queue_in_one_step(redis_env):
    _, phones = redis_env
    for body in ("a", "b"):
        await redis_client.list_push(redis_client.build_batch_queue_key(*phones),
                                     {"message_sid": body, "body": body, "received_at": time.time()})
    taken = await batch_manager.take_batch(*phones)
    assert sorted(m["body"] for m in taken) == ["a", "b"]
    assert await redis_client.list_length(redis_client.build_batch_queue_key(*phones)) == 0
    assert await batch_manager.take_batch(*phones) == []


async def test_queued_messages_are_answered_after_a_restart(redis_env):
    runs, phones = redis_env
    # Left by a container that stopped before its timer fired
    await redis_client.list_push(redis_client.build_batch_queue_key(*phones),
                                 {"message_sid": "x", "body": "¿siguen ahí?", "received_at": time.time()})
    redis = redis_client.get_redis_client()
    await redis.sadd(redis_client.BATCH_PENDING_KEY, batch_manager._pending_member(*phones))
    assert await batch_manager.recover_pending() >= 1
    assert await _wait_for(lambda: runs.batches)
    assert runs.batches[0]["body"] == "¿siguen ahí?"
    await asyncio.sleep(0.3)
    assert not await redis.sismember(redis_client.BATCH_PENDING_KEY, batch_manager._pending_member(*phones))


async def test_a_lock_is_released_only_by_its_owner(redis_env):
    key = f"test_lock:{uuid.uuid4().hex}"
    assert await redis_client.acquire_token_lock(key, "mine", 10)
    assert not await redis_client.acquire_token_lock(key, "other", 10)
    assert not await redis_client.release_token_lock(key, "other")
    assert await redis_client.release_token_lock(key, "mine")
    assert await redis_client.acquire_token_lock(key, "other", 10)
