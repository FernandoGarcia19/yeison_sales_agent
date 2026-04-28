"""
Pipeline runner that orchestrates all processing stages.
"""

from typing import List, Optional, TypedDict
from datetime import datetime
import json
import structlog

from openai import AsyncOpenAI
from langgraph.graph import StateGraph, END

from app.core.config import settings
from app.schemas.pipeline import PipelineContext, PipelineResult, PipelineStage as PipelineStageEnum
from app.services.pipeline.base import BasePipelineStage, PipelineStageError

from app.services.pipeline.stages.validator import ValidationStage
from app.services.pipeline.stages.identifier import IdentificationStage
from app.services.pipeline.stages.classifier import ClassificationStage
from app.services.pipeline.stages.context_builder import ContextBuilderStage
from app.services.pipeline.stages.action_executor import ActionExecutorStage
from app.services.pipeline.stages.response_generator import ResponseGeneratorStage

logger = structlog.get_logger()


class GraphState(TypedDict):
    context: PipelineContext
    decision: str


class PipelineRunner:
    """
    Orchestrates the message processing pipeline.

    The graph uses an agentic flow:
    - Reasoning node decides whether to use tools or respond
    - Action node runs the existing pipeline stages as tools
    - Response node generates and sends the final response
    """

    def __init__(self):
        self.logger = logger.bind(component="PipelineRunner")
        self._client: Optional[AsyncOpenAI] = None

        self._validation_stage = ValidationStage()
        self._identification_stage = IdentificationStage()
        self._classification_stage = ClassificationStage()
        self._context_builder_stage = ContextBuilderStage()
        self._action_stage = ActionExecutorStage()
        self._response_stage = ResponseGeneratorStage()

        self._tool_stages: List[BasePipelineStage] = [
            self._validation_stage,
            self._identification_stage,
            self._classification_stage,
            self._context_builder_stage,
            self._action_stage,
        ]

        self._stage_mapping = {
            "ValidationStage": PipelineStageEnum.VALIDATION,
            "IdentificationStage": PipelineStageEnum.IDENTIFICATION,
            "ClassificationStage": PipelineStageEnum.CLASSIFICATION,
            "ContextBuilderStage": PipelineStageEnum.CONTEXT_BUILDING,
            "ActionExecutorStage": PipelineStageEnum.ACTION_EXECUTION,
            "ResponseGeneratorStage": PipelineStageEnum.RESPONSE_GENERATION,
        }

        self._graph = self._build_graph()

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            if settings.use_openrouter:
                self._client = AsyncOpenAI(
                    api_key=settings.openrouter_api_key,
                    base_url="https://openrouter.ai/api/v1"
                )
            else:
                self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._client

    def _build_graph(self):
        graph = StateGraph(GraphState)
        graph.add_node("reasoning", self._reasoning_node)
        graph.add_node("action", self._action_node)
        graph.add_node("response", self._response_node)

        graph.set_entry_point("reasoning")
        graph.add_conditional_edges(
            "reasoning",
            self._route_from_reasoning,
            {
                "use_tool": "action",
                "respond": "response",
            }
        )
        graph.add_edge("action", "reasoning")
        graph.add_edge("response", END)

        return graph.compile()

    async def run(self, context: PipelineContext) -> PipelineResult:
        start_time = datetime.utcnow()

        self.logger.info(
            "pipeline_started",
            message_sid=context.message_sid,
            sender=context.sender_phone,
            stages_count=len(self._tool_stages) + 1
        )

        try:
            state: GraphState = {
                "context": context,
                "decision": "use_tool",
            }
            result_state = await self._graph.ainvoke(state)
            if isinstance(result_state, dict):
                context = result_state.get("context", context)
            else:
                context = result_state

            processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            result = PipelineResult(
                success=context.error is None,
                message_sid=context.message_sid,
                response_sent=context.response_text is not None,
                response_message_sid=context.action_result.get("message_sid") if context.action_result else None,
                intent=context.intent.value if context.intent else None,
                action_executed=context.action_type,
                error=context.error,
                processing_time_ms=processing_time
            )

            self.logger.info(
                "pipeline_finished",
                message_sid=context.message_sid,
                success=result.success,
                processing_time_ms=processing_time
            )

            return result

        except PipelineStageError as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000

            self.logger.error(
                "pipeline_stage_error",
                message_sid=context.message_sid,
                stage=e.stage,
                error=str(e),
                processing_time_ms=processing_time
            )

            return PipelineResult(
                success=False,
                message_sid=context.message_sid,
                response_sent=False,
                error=f"{e.stage}: {e.message}",
                processing_time_ms=processing_time
            )

        except Exception as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000

            self.logger.error(
                "pipeline_unexpected_error",
                message_sid=context.message_sid,
                error=str(e),
                error_type=type(e).__name__,
                processing_time_ms=processing_time
            )

            return PipelineResult(
                success=False,
                message_sid=context.message_sid,
                response_sent=False,
                error=f"Unexpected error: {str(e)}",
                processing_time_ms=processing_time
            )

    async def _reasoning_node(self, state: GraphState) -> GraphState:
        context = state["context"]

        decision, summary = await self._decide_next_step(context)
        context.agent_scratchpad.append({
            "type": "reasoning",
            "decision": decision,
            "summary": summary,
            "timestamp": datetime.utcnow().isoformat()
        })

        return {
            "context": context,
            "decision": decision,
        }

    def _route_from_reasoning(self, state: GraphState) -> str:
        return state.get("decision", "respond")

    async def _action_node(self, state: GraphState) -> GraphState:
        context = state["context"]

        for stage in self._tool_stages:
            context.current_stage = self._stage_mapping.get(stage.__class__.__name__)
            context = await stage.execute(context)

            if context.error:
                self.logger.warning(
                    "pipeline_stopped_early",
                    message_sid=context.message_sid,
                    stage=stage.stage_name,
                    error=context.error
                )
                break

        return {"context": context}

    async def _response_node(self, state: GraphState) -> GraphState:
        context = state["context"]

        if context.error:
            return {"context": context}

        context.current_stage = PipelineStageEnum.RESPONSE_GENERATION
        context = await self._response_stage.execute(context)
        return {"context": context}

    def _missing_required_context(self, context: PipelineContext) -> bool:
        if context.error:
            return False
        if context.tenant_id is None or context.agent_instance_id is None:
            return True
        if context.conversation_id is None:
            return True
        if context.agent_config is None:
            return True
        if context.intent is None:
            return True
        if context.action_type is None and context.action_result is None:
            return True
        return False

    async def _decide_next_step(self, context: PipelineContext) -> tuple[str, str]:
        if context.error:
            return "respond", "Pipeline error detected, skipping tool use."

        system_prompt, user_prompt = self._build_reasoning_prompts(context)
        required_missing = self._missing_required_context(context)

        decision = "use_tool" if required_missing else "respond"
        summary = ""

        try:
            client = self._get_client()
            model = settings.openrouter_model if settings.use_openrouter else settings.openai_model
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                extra_body={"max_completion_tokens": 200},
                response_format={"type": "json_object"}
            )

            result_text = response.choices[0].message.content.strip()
            result = json.loads(result_text)
            llm_decision = str(result.get("decision", "respond")).lower()
            llm_summary = str(result.get("reason", "")).strip()

            if llm_decision in {"use_tool", "respond"}:
                decision = llm_decision
            if llm_summary:
                summary = llm_summary

        except Exception as e:
            self.logger.error(
                "reasoning_llm_failed",
                error=str(e),
                error_type=type(e).__name__
            )

        if required_missing:
            decision = "use_tool"
            if not summary:
                summary = "Falta contexto operativo para responder."

        if decision == "respond" and not summary:
            summary = "Contexto suficiente para responder."

        return decision, summary

    def _build_reasoning_prompts(self, context: PipelineContext) -> tuple[str, str]:
        history = context.conversation_history[-3:] if context.conversation_history else []
        history_text = "\n".join(
            f"{msg.get('role', 'user')}: {msg.get('content', '')}"
            for msg in history
        )

        scratchpad = context.agent_scratchpad[-5:] if context.agent_scratchpad else []
        scratchpad_text = "\n".join(
            f"- {item.get('summary', '')}" for item in scratchpad if item.get("summary")
        )

        system_prompt = (
            "Eres un agente de control para un chatbot de ventas. Tu tarea es decidir si "
            "necesitas ejecutar herramientas (como validacion, identificacion, clasificacion, "
            "contexto o acciones) antes de responder. Responde SOLO con JSON.\n"
            "If the user wants to buy, you MUST use the get_checkout_requirements tool. "
            "You cannot provide the payment QR code until the user has provided all the "
            "required information listed by the tool. Once collected, save it using the "
            "save_checkout_data tool."
        )

        user_prompt = f"""\
MENSAJE DEL USUARIO:
"{context.message_body}"

RESUMEN DE CONTEXTO:
- intent: {context.intent.value if context.intent else None}
- action_type: {context.action_type}
- has_action_result: {context.action_result is not None}
- relevant_products: {len(context.relevant_products)}
- lead_info: {bool(context.lead_info)}
- conversation_state: {context.current_state}
- checkout_data: {context.checkout_data}

HISTORIAL RECIENTE:
{history_text if history_text else "(sin historial)"}

SCRATCHPAD RECIENTE:
{scratchpad_text if scratchpad_text else "(vacio)"}

Decide entre:
- "use_tool": Si falta informacion clave o necesitas ejecutar acciones.
- "respond": Si hay suficiente contexto para generar una respuesta final.

Responde con JSON exacto:
{{"decision": "use_tool|respond", "reason": "motivo breve"}}
"""

        return system_prompt, user_prompt

    async def run_single_stage(
        self,
        stage: BasePipelineStage,
        context: PipelineContext
    ) -> PipelineContext:
        return await stage.execute(context)
