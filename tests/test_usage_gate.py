"""Plan limit and rate limit of WhatsApp turns (Y-17), with an in-memory Redis and a fake panel."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused/unused")

import pytest  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.schemas.pipeline import PipelineContext  # noqa: E402
from app.services import usage_gate  # noqa: E402
from app.services.pipeline import runner as runner_module  # noqa: E402
from app.services.pipeline.stages import usage_gate as stage_module  # noqa: E402


class FakeRedis:
    def __init__(self):
        self.data = {}

    def pipeline(self, transaction=True):
        redis = self

        class Pipe:
            def __init__(self):
                self.ops = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def incr(self, key):
                self.ops.append(("incr", key))
                return self

            def expire(self, key, seconds):
                self.ops.append(("expire", key))
                return self

            async def execute(self):
                out = []
                for op, key in self.ops:
                    if op == "incr":
                        redis.data[key] = int(redis.data.get(key, 0)) + 1
                        out.append(redis.data[key])
                    else:
                        out.append(True)
                return out

        return Pipe()

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True


class FakePanel:
    state = "ok"
    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        FakePanel.calls += 1
        assert url.endswith("/internal/usage/sales-gate") and headers["x-internal-key"] == "k"

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"state": FakePanel.state}

        return Response()


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(usage_gate, "get_redis_client", lambda: fake)
    monkeypatch.setattr(usage_gate.httpx, "AsyncClient", FakePanel)
    monkeypatch.setattr(settings, "backend_api_url", "http://panel/api/v1")
    monkeypatch.setattr(settings, "backend_api_key", "k")
    FakePanel.state, FakePanel.calls = "ok", 0
    return fake


def _context(**extra):
    values = dict(message_sid="3EB0", sender_phone="+59170000001", recipient_phone="+59170000002",
                  message_body="hola", tenant_id=5, agent_instance_id=21,
                  agent_config={"integrations": {"supervisor_number": "+59170000009"}})
    values.update(extra)
    return PipelineContext(**values)


async def test_rate_limit_per_customer_and_per_business(redis, monkeypatch):
    monkeypatch.setattr(usage_gate, "RATE_PER_CUSTOMER", (2, 600))
    monkeypatch.setattr(usage_gate, "RATE_PER_TENANT", (3, 3600))
    assert [await usage_gate.allow_turn(5, 21, "+591A") for _ in range(3)] == [True, True, False]
    assert await usage_gate.allow_turn(5, 21, "+591B")  # another customer; the business is at 3
    assert not await usage_gate.allow_turn(5, 21, "+591C")  # 4th of the business this hour
    assert await usage_gate.allow_turn(6, 30, "+591A")


async def test_plan_state_is_cached_and_open_when_the_panel_is_down(redis, monkeypatch):
    FakePanel.state = "blocked"
    assert await usage_gate.plan_state(5) == "blocked"
    FakePanel.state = "ok"
    assert await usage_gate.plan_state(5) == "blocked" and FakePanel.calls == 1  # cached for a minute

    class Down(FakePanel):
        async def get(self, *args, **kwargs):
            raise ConnectionError("panel down")

    monkeypatch.setattr(usage_gate.httpx, "AsyncClient", Down)
    assert await usage_gate.plan_state(7) == "ok"


async def test_blocked_business_hands_off_once_and_skips_the_models(redis, monkeypatch):
    sent, notified = [], []

    async def fake_send(to, body, from_number, media_url=None):
        sent.append((to, body))
        return "SM1"

    async def fake_notify(**kwargs):
        notified.append(kwargs)
        return True

    monkeypatch.setattr(stage_module, "send_whatsapp_message", fake_send)
    monkeypatch.setattr(stage_module.NotificationService, "notify_handoff_request", staticmethod(fake_notify))
    FakePanel.state = "blocked"
    stage = stage_module.UsageGateStage()

    first = await stage.process(_context())
    assert first.halted == "plan_limit" and first.response_text == usage_gate.PLAN_LIMIT_MESSAGE
    assert sent == [("+59170000001", usage_gate.PLAN_LIMIT_MESSAGE)]
    assert notified[0]["supervisor_number"] == "+59170000009" and "límite mensual" in notified[0]["reason"]
    again = await stage.process(_context(message_sid="3EB1"))
    assert again.halted == "plan_limit" and again.response_text is None and len(sent) == 1 and len(notified) == 1

    FakePanel.state = "grace"
    redis.data.pop("usage:gate:5")
    ok = await stage.process(_context(tenant_id=5, sender_phone="+59170000003"))
    assert ok.halted is None  # within the grace the agent keeps answering


async def test_halted_turns_never_reach_the_models(redis, monkeypatch):
    class NoModel:
        @property
        def chat(self):
            raise AssertionError("the model was called")

    runner = runner_module.PipelineRunner()
    runner._client = NoModel()
    # no tenant yet: always use_tool, without asking the model (it used to ask and discard the answer)
    assert (await runner._decide_next_step(_context(tenant_id=None)))[0] == "use_tool"
    assert (await runner._decide_next_step(_context(halted="rate_limited")))[0] == "respond"
    halted = _context(halted="plan_limit")
    assert (await runner._response_node({"context": halted}))["context"].response_text is None
