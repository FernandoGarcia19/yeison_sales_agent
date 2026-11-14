"""
What each business spends on language models (Y-15).

Every OpenAI / OpenRouter call of the pipeline records one llm_usage row:
tokens in / out / cached and the cost from the price table. Recording runs
in the background and never breaks or slows a customer's turn: if it fails,
it only logs.

Prices are USD per 1M tokens (input, output, cached input). Models not in the
table are recorded without cost; LLM_PRICES (JSON, e.g.
{"gpt-5.4-mini": [0.25, 2.0, 0.025]}) adds or overrides prices without a deploy.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any, Optional

import structlog

from app.core.config import settings
from app.core.database import get_base_session_factory
from app.models.llm_usage import LlmUsage

logger = structlog.get_logger()

KNOWN_PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-4o-mini": (0.15, 0.60, 0.075),
    "gpt-4o": (2.50, 10.00, 1.25),
    "gpt-4.1-nano": (0.10, 0.40, 0.025),
    "gpt-4.1-mini": (0.40, 1.60, 0.10),
    "gpt-4.1": (2.00, 8.00, 0.50),
}

_pending: set[asyncio.Task] = set()


def _prices() -> dict[str, tuple[float, ...]]:
    prices: dict[str, tuple[float, ...]] = dict(KNOWN_PRICES)
    raw = getattr(settings, "llm_prices", None)
    if raw:
        try:
            prices.update({k: tuple(float(x) for x in v) for k, v in json.loads(raw).items()})
        except (ValueError, TypeError):
            logger.warning("llm_prices_invalid")
    return prices


def cost_of(model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> Optional[Decimal]:
    """USD for one call, or None when the model's price is unknown. 'openai/gpt-4o-mini' reads 'gpt-4o-mini'."""
    name = (model or "").split("/")[-1]
    prices = _prices()
    match = next((k for k in sorted(prices, key=len, reverse=True) if name.startswith(k)), None)
    if not match:
        return None
    price = prices[match]
    cached_price = price[2] if len(price) > 2 else price[0]
    fresh = max(input_tokens - cached_tokens, 0)
    usd = (fresh * price[0] + cached_tokens * cached_price + output_tokens * price[1]) / 1_000_000
    return Decimal(str(round(usd, 6)))


def _tokens(usage: Any) -> tuple[int, int, int]:
    """(input, output, cached) from an OpenAI usage object or dict."""
    if usage is None:
        return 0, 0, 0
    get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
    details = get("prompt_tokens_details")
    cached = (details.get("cached_tokens") if isinstance(details, dict) else getattr(details, "cached_tokens", 0)) or 0
    return int(get("prompt_tokens") or 0), int(get("completion_tokens") or 0), int(cached)


async def _write(row: dict[str, Any]) -> None:
    try:
        async with get_base_session_factory()() as db:
            db.add(LlmUsage(**row))
            await db.commit()
    except Exception as exc:  # never let accounting break a sale
        logger.warning("llm_usage_not_recorded", error=str(exc), feature=row.get("feature"))


def record(context: Any, feature: str, model: str, response: Any) -> None:
    """Record one call for the business of `context` (a PipelineContext), in the background."""
    tenant_id = getattr(context, "tenant_id", None)
    if not tenant_id:
        return  # not identified yet (e.g. a number with no agent): nothing to charge
    input_tokens, output_tokens, cached = _tokens(getattr(response, "usage", None))
    row = {"tenant_id": tenant_id, "agent_instance_id": getattr(context, "agent_instance_id", None),
           "service": "sales", "feature": feature, "model": model or "unknown",
           "input_tokens": input_tokens, "output_tokens": output_tokens, "cached_tokens": cached,
           "cost_usd": cost_of(model, input_tokens, output_tokens, cached),
           "reference": (getattr(context, "message_sid", None) or None)}
    try:
        task = asyncio.get_running_loop().create_task(_write(row))
    except RuntimeError:
        return
    _pending.add(task)
    task.add_done_callback(_pending.discard)
