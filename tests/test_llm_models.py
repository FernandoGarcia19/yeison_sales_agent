"""Model per pipeline step and prompt cache key (Y-16)."""

import json
import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused/unused")

from app.core.config import settings  # noqa: E402
from app.schemas.pipeline import PipelineContext  # noqa: E402
from app.services import llm_usage  # noqa: E402
from app.services.pipeline import llm_models  # noqa: E402
from app.services.pipeline.stages.classifier import ClassificationStage  # noqa: E402


def _context(**extra):
    return PipelineContext(message_sid="3EB0", sender_phone="+59170000001", recipient_phone="+59170000002",
                           message_body="hola", tenant_id=5, agent_instance_id=21, **extra)


def test_internal_steps_use_the_small_model_and_the_reply_the_main_one(monkeypatch):
    monkeypatch.setattr(settings, "use_openrouter", False)
    monkeypatch.setattr(settings, "openai_model", "gpt-5.4-mini")
    monkeypatch.setattr(settings, "openai_small_model", None)
    assert {llm_models.model_for(s) for s in ("classification", "decision", "response")} == {"gpt-5.4-mini"}

    monkeypatch.setattr(settings, "openai_small_model", "gpt-5.4-nano")
    for step in ("classification", "decision", "checkout_extraction", "item_extraction"):
        assert llm_models.model_for(step) == "gpt-5.4-nano"
    assert llm_models.model_for("response") == "gpt-5.4-mini"

    monkeypatch.setattr(settings, "use_openrouter", True)
    assert llm_models.model_for("classification") == settings.openrouter_model


def test_cache_key_per_agent_and_step_only_on_openai(monkeypatch):
    monkeypatch.setattr(settings, "use_openrouter", False)
    monkeypatch.setattr(settings, "prompt_cache_enabled", True)
    body = llm_models.request_options(_context(), "response", max_completion_tokens=500)
    assert body == {"max_completion_tokens": 500, "prompt_cache_key": "yeison-agent-21-response"}
    assert llm_models.request_options(None, "decision", max_completion_tokens=200) == {"max_completion_tokens": 200}
    monkeypatch.setattr(settings, "prompt_cache_enabled", False)
    assert "prompt_cache_key" not in llm_models.request_options(_context(), "response")
    monkeypatch.setattr(settings, "prompt_cache_enabled", True)
    monkeypatch.setattr(settings, "use_openrouter", True)
    assert "prompt_cache_key" not in llm_models.request_options(_context(), "response")


async def test_the_classifier_calls_the_small_model_with_the_cache_key(monkeypatch):
    monkeypatch.setattr(settings, "use_openrouter", False)
    monkeypatch.setattr(settings, "prompt_cache_enabled", True)
    monkeypatch.setattr(settings, "openai_small_model", "gpt-5.4-nano")
    recorded = []
    monkeypatch.setattr(llm_usage, "record", lambda context, feature, model, response: recorded.append((feature, model)))
    calls = []
    answers = ["pricing_question", "consultar precio de ibuprofeno"]

    async def create(**kwargs):
        calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps({"intent": answers.pop(0), "confidence": 0.9}))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    stage = ClassificationStage()
    stage._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert await stage._classify_intent_ai("¿cuánto cuesta?", [], context=_context()) == ("pricing_question", 0.9)
    assert calls[0]["model"] == "gpt-5.4-nano"
    assert calls[0]["extra_body"] == {"max_completion_tokens": 200, "prompt_cache_key": "yeison-agent-21-classification"}
    assert recorded[0] == ("sales.classification", "gpt-5.4-nano")
    # an intent outside the list (small models do this) is "unknown", not an error
    assert await stage._classify_intent_ai("¿cuánto cuesta?", [], context=_context()) == ("unknown", 0.3)
