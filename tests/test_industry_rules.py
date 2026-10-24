"""The rules of the business type (written by the panel) reach the agent's prompt (Etapa 3)."""

from types import SimpleNamespace

from app.schemas.pipeline import PipelineContext
from app.services.pipeline.stages.context_builder import ContextBuilderStage
from app.services.pipeline.stages.response_generator import ResponseGeneratorStage

RULES = ["Nunca recomiendes medicamentos, dosis ni tratamientos: eso lo decide un profesional de salud.",
         "Para medicamentos que requieren receta, pide una foto de la receta antes de confirmar el pedido."]


def _tenant_config(business):
    return SimpleNamespace(business=business, contact=None, products=None, operations=None)


def test_rules_are_merged_into_the_tenant_info():
    config = ContextBuilderStage()._merge_configurations(
        {}, _tenant_config({"company_name": "Farmacia Sol", "industry": "Farmacia y salud", "industry_rules": RULES + ["  ", ""]}))
    assert config["tenant_info"]["industry_rules"] == RULES
    plain = ContextBuilderStage()._merge_configurations({}, _tenant_config({"company_name": "Farmacia Sol"}))
    assert "industry_rules" not in plain["tenant_info"]


def _prompt(agent_config):
    context = PipelineContext(message_sid="m1", sender_phone="+59170000001", recipient_phone="+59170000009",
                              message_body="hola", agent_config=agent_config)
    return ResponseGeneratorStage()._build_system_prompt(context)


def test_the_prompt_lists_the_rules():
    config = ContextBuilderStage()._merge_configurations(
        {}, _tenant_config({"company_name": "Farmacia Sol", "industry": "Farmacia y salud", "industry_rules": RULES}))
    prompt = _prompt(config)
    assert "REGLAS DE NUESTRO RUBRO" in prompt
    assert all(f"- {rule}" in prompt for rule in RULES)


def test_no_rules_no_section():
    config = ContextBuilderStage()._merge_configurations({}, _tenant_config({"company_name": "Farmacia Sol"}))
    assert "REGLAS DE NUESTRO RUBRO" not in _prompt(config)
