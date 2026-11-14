"""
Which model each pipeline step uses, and prompt caching (Y-16).

The internal steps (classification, decision, extraction) only return a small
JSON, so they can run on a small model (OPENAI_SMALL_MODEL, e.g. gpt-5.4-nano);
the reply the customer reads stays on OPENAI_MODEL. Without OPENAI_SMALL_MODEL
every step uses OPENAI_MODEL, as before.

OpenAI caches identical prompt prefixes of 1024+ tokens on its own. The prompts
put what is fixed per agent first (system prompt, catalog) and the message
last; `prompt_cache_key` (agent + step) sends the same agent's calls to the
same cache so those prefixes are hit more often. The cached tokens are
recorded in llm_usage (Y-15).
"""

from typing import Any, Optional

from app.core.config import settings
from app.schemas.pipeline import PipelineContext

SMALL_STEPS = {"classification", "decision", "checkout_extraction", "item_extraction"}


def model_for(step: str) -> str:
    if settings.use_openrouter:
        return settings.openrouter_model
    if step in SMALL_STEPS and settings.openai_small_model:
        return settings.openai_small_model
    return settings.openai_model


def request_options(context: Optional[PipelineContext], step: str, **body: Any) -> dict:
    """extra_body for chat.completions.create: the given options plus the cache key, on OpenAI only."""
    agent_id = getattr(context, "agent_instance_id", None)
    if settings.prompt_cache_enabled and not settings.use_openrouter and agent_id:
        body["prompt_cache_key"] = f"yeison-agent-{agent_id}-{step}"
    return body
