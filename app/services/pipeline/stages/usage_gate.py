"""
Stage after identification: the plan limit and rate limit of the business (Y-17).

A halted turn skips the rest of the pipeline and the model: rate limited turns
get no answer; past the plan limit the customer gets the fixed handoff message
and the supervisor is told (both once every few hours per customer).
"""

from app.integrations.whatsapp.client import send_whatsapp_message
from app.schemas.pipeline import PipelineContext
from app.services import usage_gate
from app.services.notification_service import NotificationService
from app.services.pipeline.base import BasePipelineStage


class UsageGateStage(BasePipelineStage):
    async def process(self, context: PipelineContext) -> PipelineContext:
        if context.error or not context.tenant_id or context.halted:
            return context
        if not await usage_gate.allow_turn(context.tenant_id, context.agent_instance_id, context.sender_phone):
            context.halted = "rate_limited"
            return context
        if await usage_gate.plan_state(context.tenant_id) != "blocked":
            return context

        context.halted = "plan_limit"
        if not await usage_gate.first_handoff(context.agent_instance_id, context.sender_phone):
            self.log_info("plan_limit_turn_skipped", tenant_id=context.tenant_id, customer=context.sender_phone)
            return context
        try:
            context.response_text = usage_gate.PLAN_LIMIT_MESSAGE
            await send_whatsapp_message(to=context.sender_phone, body=usage_gate.PLAN_LIMIT_MESSAGE,
                                        from_number=context.recipient_phone)
        except Exception as exc:
            self.log_error("plan_limit_message_failed", error=str(exc))
        supervisor = ((context.agent_config or {}).get("integrations") or {}).get("supervisor_number")
        if supervisor:
            try:
                await NotificationService.notify_handoff_request(
                    supervisor_number=supervisor, agent_phone=context.recipient_phone,
                    customer_phone=context.sender_phone, customer_name=context.profile_name,
                    reason="Se alcanzó el límite mensual del plan: el agente ya no responde con IA. Atiende a este cliente.",
                    conversation_context=context.message_body)
            except Exception as exc:
                self.log_error("plan_limit_handoff_failed", error=str(exc))
        self.log_info("plan_limit_handoff", tenant_id=context.tenant_id, customer=context.sender_phone)
        return context
