"""LLM usage per business (Y-15): cost from tokens, recorded in the background, never in the way."""

import asyncio
import os
from decimal import Decimal
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused/unused")

from app.core.config import settings  # noqa: E402
from app.services import llm_usage  # noqa: E402


def test_cost_of_known_models_and_overrides(monkeypatch):
    # 1000 in (200 of them cached), 500 out with gpt-4o-mini: 800*0.15 + 200*0.075 + 500*0.60 per 1M
    assert llm_usage.cost_of("gpt-4o-mini", 1000, 500, 200) == Decimal("0.000435")
    assert llm_usage.cost_of("openai/gpt-4o-mini-2024-07-18", 1_000_000, 0) == Decimal("0.15")
    assert llm_usage.cost_of("gpt-4o", 0, 1_000_000) == Decimal("10.0")
    assert llm_usage.cost_of("gpt-5.4-mini", 1000, 1000) is None  # unknown price: tokens only
    monkeypatch.setattr(settings, "llm_prices", '{"gpt-5.4-mini": [0.25, 2.0, 0.025]}')
    assert llm_usage.cost_of("gpt-5.4-mini", 1_000_000, 1_000_000) == Decimal("2.25")


async def test_record_writes_one_row_per_call(monkeypatch):
    rows = []

    async def fake_write(row):
        rows.append(row)

    monkeypatch.setattr(llm_usage, "_write", fake_write)
    context = SimpleNamespace(tenant_id=7, agent_instance_id=3, message_sid="3EB0AA")
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=80,
                                                     prompt_tokens_details=SimpleNamespace(cached_tokens=1024)))
    llm_usage.record(context, "sales.classification", "gpt-4o-mini", response)
    llm_usage.record(SimpleNamespace(tenant_id=None), "sales.response", "gpt-4o-mini", response)  # no tenant: skipped
    await asyncio.sleep(0)
    assert len(rows) == 1
    row = rows[0]
    assert (row["tenant_id"], row["agent_instance_id"], row["feature"], row["service"]) == (7, 3, "sales.classification", "sales")
    assert (row["input_tokens"], row["output_tokens"], row["cached_tokens"]) == (1200, 80, 1024)
    assert row["reference"] == "3EB0AA" and row["cost_usd"] == llm_usage.cost_of("gpt-4o-mini", 1200, 80, 1024)


async def test_a_failing_write_does_not_raise(monkeypatch):
    def broken_factory():
        raise RuntimeError("db down")

    monkeypatch.setattr(llm_usage, "get_session_factory", broken_factory)
    await llm_usage._write({"tenant_id": 1, "feature": "x"})  # only logs
