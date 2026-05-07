"""
Action executor stage - executes actions based on intent.
"""

import json
from typing import Optional, List
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from openai import AsyncOpenAI

from app.schemas.pipeline import PipelineContext, IntentType, ConversationState
from app.services.pipeline.base import BasePipelineStage, ActionExecutionError
from app.services.notification_service import NotificationService
from app.models import Lead, SalesConversation
from app.core.database import get_session_factory
from app.core.config import settings


class ActionExecutorStage(BasePipelineStage):
    """
    Stage 5: Execute actions based on classified intent.

    Actions:
    - Check inventory availability
    - Create/update lead (via main backend API)
    - Schedule appointment (via main backend API)
    - Send product information
    - Log interaction
    """

    def __init__(self):
        super().__init__()
        self._client: Optional[AsyncOpenAI] = None

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

    async def process(self, context: PipelineContext) -> PipelineContext:
        """Execute action based on intent."""

        self.log_info(
            "executing_action",
            intent=context.intent.value if context.intent else None,
            action_type=context.action_type
        )

        # If waiting for payment proof, only allow payment_proof_received through
        if context.current_state == ConversationState.AWAITING_RECEIPT:
            if context.action_type != "payment_proof_received":
                proof_submitted = context.cart_contents.get("payment_proof_submitted", False)
                if proof_submitted:
                    context.action_type = "payment_review_pending"
                    context.action_result = {"action": "payment_review_pending"}
                else:
                    context.action_type = "awaiting_receipt_reminder"
                    context.action_result = {"action": "awaiting_receipt_reminder"}
                self.log_info("awaiting_receipt_non_proof_message", action_type=context.action_type)
                return context

        # If actively collecting checkout data, handle extraction/completion first.
        # Skip if action is already payment_proof_received — let it fall through to its handler.
        if context.current_state == ConversationState.FULFILLMENT_COORD and context.action_type != "payment_proof_received":
            await self._handle_checkout_collection(context)
            if context.action_result is not None:
                self.log_info("action_executed", action_type=context.action_type)
                return context

        # Check if this is a payment proof submission (special case)
        if context.action_type == "payment_proof_received":
            await self._handle_payment_proof_received(context)
        
        # Route to appropriate action based on intent
        elif context.intent == IntentType.PRODUCT_INQUIRY:
            await self._handle_product_inquiry(context)
        
        elif context.intent == IntentType.PRICING_QUESTION:
            await self._handle_pricing_question(context)
        
        elif context.intent == IntentType.AVAILABILITY_CHECK:
            await self._handle_availability_check(context)
        
        elif context.intent == IntentType.PURCHASE_INTENT:
            await self._handle_purchase_intent(context)
        
        elif context.intent == IntentType.GREETING:
            await self._handle_greeting(context)
        
        elif context.intent == IntentType.CLOSING:
            await self._handle_closing(context)
        
        elif context.intent == IntentType.HANDOFF_REQUEST:
            await self._handle_handoff_request(context)
        
        else:
            await self._handle_general_question(context)
        
        self.log_info(
            "action_executed",
            action_type=context.action_type
        )
        
        return context
    
    async def _handle_product_inquiry(self, context: PipelineContext):
        """Handle product inquiry intent."""
        context.action_type = "product_inquiry"
        context.action_result = {
            "products": context.relevant_products
        }
    
    async def _handle_pricing_question(self, context: PipelineContext):
        """Handle pricing question intent."""
        context.action_type = "pricing_inquiry"
        context.action_result = {
            "products": context.relevant_products
        }
    
    async def _handle_availability_check(self, context: PipelineContext):
        """Handle availability check intent."""
        context.action_type = "availability_check"
        context.action_result = {
            "products": context.relevant_products,
            "available_count": len(context.relevant_products)
        }
    
    async def _handle_purchase_intent(self, context: PipelineContext):
        """
        Handle purchase intent - manages payment flow based on configuration.
        
        If QR payment is enabled:
        1. Send QR code to customer
        2. Wait for payment proof (image/document)
        3. Forward proof to supervisor with purchase details
        
        If QR payment is disabled:
        1. Notify supervisor of confirmed sale
        2. Mark lead as converted
        """
        context.action_type = "purchase_intent"

        is_purchase_confirmed = await self._is_purchase_confirmed(context)

        # If a previous purchase cycle is complete, start a fresh one when the
        # customer is confirming a new purchase.
        if context.current_state == ConversationState.ORDER_COMPLETED:
            if is_purchase_confirmed:
                await self._start_new_purchase_cycle(context)
            else:
                context.action_result = {"action": "order_already_completed"}
                return

        if not is_purchase_confirmed:
            # Just qualify the lead, not ready to purchase yet
            context.action_result = {
                "lead_id": context.lead_info.get("id") if context.lead_info else None,
                "action": "qualify_lead",
                "stage": "considering"
            }
            return

        # Capture which products (and quantities) the user wants — only on the
        # first turn of this purchase intent so we don't overwrite a stored cart.
        if not context.cart_contents.get("items"):
            extracted = await self._extract_purchase_items(
                context.message_body, context.conversation_history, context.relevant_products
            )
            selected = []
            for entry in extracted:
                prod = next(
                    (p for p in context.relevant_products if str(p.get("id")) == str(entry.get("id"))),
                    None,
                )
                if prod:
                    selected.append({**prod, "quantity": int(entry.get("quantity", 1))})
            if not selected and len(context.relevant_products) == 1:
                # Single product available — treat it as the selection
                selected = [{**context.relevant_products[0], "quantity": 1}]
            if selected:
                context.cart_contents["items"] = selected

        # Gate: all checkout data must be collected before payment
        required_fields = self._get_checkout_requirements(context)
        missing_fields = [f for f in required_fields if not context.checkout_data.get(f)]
        if missing_fields:
            context.current_state = ConversationState.FULFILLMENT_COORD
            context.action_type = "collect_checkout_data"
            context.action_result = {
                "action": "collect_checkout_data",
                "required_fields": required_fields,
                "missing_fields": missing_fields,
                "collected": context.checkout_data,
            }
            return

        # Purchase is confirmed and checkout complete — check payment method
        qr_payment_enabled = await self._is_qr_payment_enabled(context)

        if qr_payment_enabled:
            await self._handle_qr_payment_flow(context)
        else:
            lead_id = await self._mark_lead_as_converted(context)
            await self._notify_supervisor_sale_completed(context)
            context.current_state = ConversationState.ORDER_COMPLETED
            context.action_result = {
                "lead_id": lead_id,
                "action": "sale_completed",
                "payment_method": "non_qr",
                "notified": True,
            }
    
    async def _is_purchase_confirmed(self, context: PipelineContext) -> bool:
        """
        Determine if the customer has confirmed their purchase.
        
        Uses keyword-based detection to identify purchase confirmation.
        Does NOT require products in context since customer may have already
        seen product info in previous messages.
        """
        message_lower = context.message_body.lower()
        
        # Keywords that indicate purchase confirmation
        confirmation_keywords = [
            "confirmo",
            "confirmado",
            "confirmar",
            "compro",
            "comprar",
            "acepto",
            "si, lo quiero",
            "lo quiero",
            "proceder con la compra",
            "realizar el pago",
            "hacer el pago",
            "pagar ahora",
            "enviar",
            "envío",
            "quiero comprar",
            "voy a comprar",
            "compro el plan",
            "quiero el plan",
        ]
        
        # Check if any confirmation keyword is in the message
        for keyword in confirmation_keywords:
            if keyword in message_lower:
                return True
        
        return False
    
    async def _is_qr_payment_enabled(self, context: PipelineContext) -> bool:
        """
        Check if QR payment is enabled for the active agent.
        Reads agent_instance.configuration.sales_process.QR_payment.
        """
        try:
            from app.core.database import get_session_factory
            from app.models import AgentInstance
            from sqlalchemy import select

            if not context.agent_instance_id:
                return False

            session_factory = get_session_factory()
            async with session_factory() as db:
                stmt = select(AgentInstance).where(AgentInstance.id == context.agent_instance_id)
                result = await db.execute(stmt)
                agent = result.scalar_one_or_none()

                if not agent or not agent.configuration:
                    return False

                sales_process = (agent.configuration or {}).get("sales_process") or {}
                return bool(sales_process.get("QR_payment"))

        except Exception as e:
            self.log_error(
                "failed_to_check_qr_payment",
                tenant_id=context.tenant_id,
                agent_instance_id=context.agent_instance_id,
                error=str(e)
            )
            return False

    async def _get_qr_payment_url(self, context: PipelineContext) -> str:
        """
        Return a presigned R2 URL (valid 15 min) for the QR code attached to the active agent.
        Reads agent_instance.configuration.sales_process.QR_code (R2 object key).
        Falls back to the legacy ConfigurationTenant location for tenants that have not yet
        re-uploaded their QR via the new per-agent endpoint.
        """
        try:
            from app.core.database import get_session_factory
            from app.models import AgentInstance, ConfigurationTenant
            from app.services import r2_storage
            from sqlalchemy import select

            session_factory = get_session_factory()
            async with session_factory() as db:
                if context.agent_instance_id:
                    agent_stmt = select(AgentInstance).where(
                        AgentInstance.id == context.agent_instance_id
                    )
                    agent_result = await db.execute(agent_stmt)
                    agent = agent_result.scalar_one_or_none()

                    if agent and agent.configuration:
                        sales_process = (agent.configuration or {}).get("sales_process") or {}
                        object_key = sales_process.get("QR_code")
                        if object_key:
                            return r2_storage.get_presigned_url(object_key, expires_in=900)

                # Legacy fallback: tenant-level configuration_tenant.products.qr_object_key
                stmt = (
                    select(ConfigurationTenant)
                    .where(ConfigurationTenant.tenant_id == context.tenant_id)
                    .where(ConfigurationTenant.active == True)
                )
                result = await db.execute(stmt)
                config = result.scalar_one_or_none()

                if not config:
                    return ""

                object_key = config.get_qr_object_key()
                if object_key:
                    self.log_info(
                        "qr_legacy_fallback_used",
                        tenant_id=context.tenant_id,
                        agent_instance_id=context.agent_instance_id,
                    )
                    return r2_storage.get_presigned_url(object_key, expires_in=900)

                return config.get_qr_payment_url()

        except Exception as e:
            self.log_error(
                "failed_to_get_qr_payment_url",
                tenant_id=context.tenant_id,
                agent_instance_id=context.agent_instance_id,
                error=str(e)
            )
            return ""
    
    async def _handle_qr_payment_flow(self, context: PipelineContext):
        """
        Handle QR payment flow:
        1. Send QR code to customer
        2. Mark action as waiting for payment proof
        3. Response generator will wait for next message (payment proof)
        """
        qr_url = await self._get_qr_payment_url(context)
        
        self.log_info(
            "qr_payment_flow_started",
            tenant_id=context.tenant_id,
            qr_enabled=True,
            qr_url_present=bool(qr_url)
        )
        
        if not qr_url:
            self.log_error(
                "qr_payment_enabled_but_no_url",
                tenant_id=context.tenant_id
            )
            context.action_result = {
                "error": "QR URL not configured",
                "action": "qr_payment_failed"
            }
            return
        
        # Send QR to customer via NotificationService
        try:
            customer_name = context.profile_name or (
                context.lead_info.get("name") if context.lead_info else None
            )
            
            self.log_info(
                "sending_qr_code_to_customer",
                customer=context.sender_phone,
                customer_name=customer_name,
                qr_url=qr_url,
                agent=context.recipient_phone
            )
            
            await NotificationService.send_qr_payment_request(
                customer_phone=context.sender_phone,
                agent_phone=context.recipient_phone,
                qr_image_url=qr_url,
                customer_name=customer_name
            )
            
            context.action_result = {
                "action": "awaiting_payment_proof",
                "qr_sent": True,
                "stage": "payment_pending",
                "qr_url": qr_url
            }
            context.current_state = ConversationState.AWAITING_RECEIPT
            # QR message already sent via Twilio — response_generator must not send again.
            context.response_already_sent = True
            context.response_text = "📲 [QR de pago enviado al cliente]"
            
            self.log_info(
                "qr_payment_flow_initiated",
                customer=context.sender_phone,
                lead_id=context.lead_id,
                qr_url=qr_url
            )
            
        except Exception as e:
            self.log_error(
                "failed_to_send_qr_payment",
                customer=context.sender_phone,
                error=str(e),
                error_type=type(e).__name__
            )
            context.action_result = {
                "action": "qr_payment_failed",
                "error": str(e)
            }
    
    async def _is_sale_complete(self, context: PipelineContext) -> bool:
        """
        Determine if the purchase intent represents a completed sale.
        
        This can be enhanced with more sophisticated logic:
        - Check if payment confirmation was mentioned
        - Check if customer confirmed the order
        - Look for specific keywords in the message
        """
        message_lower = context.message_body.lower()
        
        # Keywords that indicate a completed sale
        completion_keywords = [
            "confirmo",
            "confirmado",
            "confirmar",
            "compro",
            "comprar",
            "acepto",
            "si, lo quiero",
            "lo quiero",
            "proceder con la compra",
            "realizar el pago",
            "hacer el pago",
            "pagar ahora",
            "enviar",
            "envío",
        ]
        
        # Check if any completion keyword is in the message
        for keyword in completion_keywords:
            if keyword in message_lower:
                # Also check if we have products in context
                if context.relevant_products:
                    return True
        
        return False

    def _get_checkout_requirements(self, context: PipelineContext) -> List[str]:
        """Return list of required checkout fields from agent config."""
        reqs = context.agent_config.get("checkout_requirements") if context.agent_config else None
        if isinstance(reqs, list) and reqs:
            return reqs
        return ["Nombre completo", "Dirección de entrega", "NIT"]

    async def _extract_checkout_fields(
        self,
        message: str,
        required_fields: List[str],
        already_collected: dict,
    ) -> dict:
        """Use LLM to extract checkout fields present in the user's message."""
        missing = [f for f in required_fields if not already_collected.get(f)]
        if not missing or not message.strip():
            return {}

        fields_str = ", ".join(f'"{f}"' for f in missing)
        prompt = (
            f'Extract from the user\'s message the following information if explicitly provided: {fields_str}.\n'
            f'Return a JSON object using the exact field names as keys, containing only fields clearly mentioned.\n'
            f'If a field was not mentioned, omit it.\n\n'
            f'User message: "{message}"\n\n'
            f'Return only valid JSON.'
        )

        try:
            client = self._get_client()
            model = settings.openrouter_model if settings.use_openrouter else settings.openai_model
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                extra_body={"max_completion_tokens": 200},
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content.strip())
            return {k: v for k, v in result.items() if k in required_fields and v}
        except Exception as e:
            self.log_error("checkout_extraction_failed", error=str(e))
            return {}

    async def _extract_purchase_items(
        self,
        message: str,
        conversation_history: list,
        available_products: list,
    ) -> list:
        """Use LLM to identify which products (and quantities) the user wants to buy."""
        if not available_products or not message.strip():
            return []

        products_str = "\n".join(
            f'- id={p.get("id")}, name="{p.get("name") or p.get("product_name", "")}", price={p.get("price", 0)}'
            for p in available_products
        )
        history_str = ""
        if conversation_history:
            recent = conversation_history[-5:]
            history_str = "\n".join(
                f'{"Usuario" if m.get("role") == "user" else "Agente"}: {m.get("content", "")}'
                for m in recent
            )

        prompt = (
            "From the conversation below, identify which products the user wants to purchase and in what quantity.\n"
            'Return a JSON object with key "items" containing a list of {"id": <product_id>, "quantity": <int>}.\n'
            "Only include products explicitly chosen by the user. Default quantity to 1 if not stated.\n"
            'If no specific product is identifiable return {"items": []}.\n\n'
            f"Available products:\n{products_str}\n\n"
            + (f"Recent conversation:\n{history_str}\n\n" if history_str else "")
            + f'Latest user message: "{message}"\n\nReturn only valid JSON.'
        )

        try:
            client = self._get_client()
            model = settings.openrouter_model if settings.use_openrouter else settings.openai_model
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                extra_body={"max_completion_tokens": 200},
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content.strip())
            return result.get("items", [])
        except Exception as e:
            self.log_error("purchase_item_extraction_failed", error=str(e))
            return []

    async def _start_new_purchase_cycle(self, context: PipelineContext) -> None:
        """Create a new lead and reset the cart for a fresh purchase cycle."""
        clean_phone = context.sender_phone.replace("whatsapp:", "")
        customer_name = context.profile_name or (
            context.lead_info.get("name") if context.lead_info else None
        )
        new_lead = Lead(
            tenant_id=context.tenant_id,
            agent_instance_id=context.agent_instance_id,
            phone=clean_phone,
            name=customer_name,
            source="whatsapp",
            status="new",
            conversation_id=context.conversation_id,
        )
        from sqlalchemy import update as sa_update
        session_factory = get_session_factory()
        async with session_factory() as db:
            db.add(new_lead)
            await db.flush()
            await db.execute(
                sa_update(SalesConversation)
                .where(SalesConversation.id == context.conversation_id)
                .values(
                    cart_contents={},
                    current_state=ConversationState.BROWSING.value,
                )
            )
            await db.commit()
            await db.refresh(new_lead)

        context.lead_id = new_lead.id
        context.lead_info = {
            "id": new_lead.id,
            "name": new_lead.name,
            "phone": new_lead.phone,
            "status": new_lead.status,
            "score": None,
        }
        context.cart_contents = {}
        context.checkout_data = {}
        context.current_state = ConversationState.BROWSING
        self.log_info(
            "new_purchase_cycle_started",
            new_lead_id=new_lead.id,
            conversation_id=context.conversation_id,
        )

    async def _handle_checkout_collection(self, context: PipelineContext) -> None:
        """Extract any provided checkout fields and decide next step."""
        required_fields = self._get_checkout_requirements(context)

        extracted = await self._extract_checkout_fields(
            context.message_body, required_fields, context.checkout_data
        )
        if extracted:
            context.checkout_data.update(extracted)
            context.cart_contents["checkout_data"] = context.checkout_data
            self.log_info("checkout_fields_extracted", fields=list(extracted.keys()))

        missing_fields = [f for f in required_fields if not context.checkout_data.get(f)]

        if missing_fields:
            context.action_type = "collect_checkout_data"
            context.action_result = {
                "action": "collect_checkout_data",
                "required_fields": required_fields,
                "missing_fields": missing_fields,
                "collected": context.checkout_data,
            }
        else:
            # All data collected — proceed with payment
            qr_enabled = await self._is_qr_payment_enabled(context)
            if qr_enabled:
                await self._handle_qr_payment_flow(context)
            else:
                lead_id = await self._mark_lead_as_converted(context)
                await self._notify_supervisor_sale_completed(context)
                context.current_state = ConversationState.ORDER_COMPLETED
                context.action_result = {
                    "lead_id": lead_id,
                    "action": "sale_completed",
                    "payment_method": "non_qr",
                    "notified": True,
                }

    async def _mark_lead_as_converted(self, context: PipelineContext) -> Optional[int]:
        """Mark the lead as converted in the database."""
        
        if not context.lead_id:
            self.log_error("no_lead_to_convert", context=context)
            return None
        
        try:
            session_factory = get_session_factory()
            async with session_factory() as db:
                # Update lead status
                stmt = (
                    update(Lead)
                    .where(Lead.id == context.lead_id)
                    .values(
                        status="converted",
                        converted_at=datetime.utcnow(),
                        last_update=datetime.utcnow()
                    )
                )
                await db.execute(stmt)
                await db.commit()
                
                self.log_info(
                    "lead_converted",
                    lead_id=context.lead_id
                )
                
                return context.lead_id
                
        except Exception as e:
            self.log_error(
                "failed_to_convert_lead",
                lead_id=context.lead_id,
                error=str(e)
            )
            return None
    
    async def _notify_supervisor_sale_completed(self, context: PipelineContext):
        """Send notification to supervisor about completed sale."""
        
        # Get supervisor number from agent config
        supervisor_number = context.agent_config.get("integrations", {}).get("supervisor_number")
        
        if not supervisor_number:
            self.log_error(
                "no_supervisor_number_configured",
                agent_instance_id=context.agent_instance_id
            )
            return
        
        # Prepare customer information
        customer_name = context.profile_name or context.lead_info.get("name") if context.lead_info else None
        
        # Prepare conversation summary (last few messages)
        conversation_summary = self._create_conversation_summary(context.conversation_history)
        
        # Send notification
        try:
            products = context.cart_contents.get("items") or context.relevant_products
            await NotificationService.notify_sale_completed(
                supervisor_number=supervisor_number,
                agent_phone=context.recipient_phone,
                customer_phone=context.sender_phone,
                customer_name=customer_name,
                products=products,
                lead_info=context.lead_info,
                conversation_summary=conversation_summary
            )
            
            self.log_info(
                "supervisor_notified_sale",
                supervisor=supervisor_number,
                customer=context.sender_phone
            )
            
        except Exception as e:
            self.log_error(
                "failed_to_notify_supervisor",
                error=str(e),
                supervisor=supervisor_number
            )
    
    def _create_conversation_summary(self, conversation_history: list) -> str:
        """Create a brief summary of the conversation."""
        
        if not conversation_history:
            return "Sin historial de conversación"
        
        # Get last 3 messages
        recent_messages = conversation_history[-3:]
        
        summary_parts = []
        for msg in recent_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:100]  # Limit to 100 chars
            summary_parts.append(f"{role}: {content}")
        
        return "\n".join(summary_parts)
    
    async def _handle_greeting(self, context: PipelineContext):
        """Handle greeting intent."""
        context.action_type = "greeting"
        context.action_result = {
            "is_first_message": len(context.conversation_history) == 0
        }
    
    async def _handle_closing(self, context: PipelineContext):
        """Handle closing intent."""
        context.action_type = "closing"
        context.action_result = {}
    
    async def _handle_handoff_request(self, context: PipelineContext):
        """
        Handle handoff request - customer wants to talk to a human.
        Notifies supervisor immediately.
        """
        context.action_type = "handoff_request"
        
        # Get supervisor number from agent config
        supervisor_number = context.agent_config.get("integrations", {}).get("supervisor_number")
        
        if supervisor_number:
            customer_name = context.profile_name or context.lead_info.get("name") if context.lead_info else None
            conversation_context = self._create_conversation_summary(context.conversation_history)
            
            try:
                await NotificationService.notify_handoff_request(
                    supervisor_number=supervisor_number,
                    agent_phone=context.recipient_phone,
                    customer_phone=context.sender_phone,
                    customer_name=customer_name,
                    reason="Cliente solicitó hablar con un humano",
                    conversation_context=conversation_context
                )
                
                self.log_info(
                    "handoff_notification_sent",
                    supervisor=supervisor_number,
                    customer=context.sender_phone
                )
                
                context.action_result = {"notified": True}
                
            except Exception as e:
                self.log_error(
                    "failed_to_notify_handoff",
                    error=str(e)
                )
                context.action_result = {"notified": False, "error": str(e)}
        else:
            self.log_error("no_supervisor_for_handoff")
            context.action_result = {"notified": False, "error": "No supervisor configured"}
    
    async def _handle_payment_proof_received(self, context: PipelineContext):
        """
        Handle payment proof submission from customer.

        Flow:
        1. Download file bytes from Twilio (authenticated).
        2. Archive to private R2 bucket for permanent audit trail.
        3. Mark lead as converted and save receipt_object_key.
        4. WhatsApp supervisor notification via presigned R2 URL (valid 15 min,
           enough for Twilio to fetch it immediately).
        5. Telegram native upload — supervisor sees the file inline regardless of
           when they open their phone.
        """
        context.action_type = "payment_proof_received"

        if not context.media_urls:
            self.log_error("payment_proof_no_media", lead_id=context.lead_id)
            context.action_result = {"error": "No payment proof found", "action": "payment_proof_invalid"}
            return

        try:
            import mimetypes
            from datetime import timezone
            from app.services.media_downloader import download_twilio_media
            from app.services import r2_storage
            from app.services import telegram_service
            from app.core.config import settings

            proof_twilio_url = context.media_urls[0]

            # 1. Download from Twilio
            file_bytes, content_type = await download_twilio_media(proof_twilio_url)

            # 2. Upload to R2
            ext = (mimetypes.guess_extension(content_type) or ".bin").lstrip(".")
            # mimetypes returns .jpe for image/jpeg — normalize
            if ext == "jpe":
                ext = "jpeg"
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            object_key = f"receipts/tenant_{context.tenant_id}/lead_{context.lead_id}_{timestamp}.{ext}"
            await r2_storage.upload_bytes(object_key, file_bytes, content_type)

            self.log_info("receipt_archived_to_r2", object_key=object_key, lead_id=context.lead_id)

            # 3. Mark lead as converted and persist receipt key
            lead_id = await self._mark_lead_as_converted(context)
            await self._save_receipt_object_key(lead_id, object_key)

            # 4. WhatsApp supervisor notification using a short-lived presigned URL
            supervisor_number = context.agent_config.get("integrations", {}).get("supervisor_number")
            presigned_url = r2_storage.get_presigned_url(object_key, expires_in=900)

            if supervisor_number:
                customer_name = context.profile_name or (
                    context.lead_info.get("name") if context.lead_info else None
                )
                proof_products = context.cart_contents.get("items") or context.relevant_products
                await NotificationService.forward_payment_proof_to_supervisor(
                    supervisor_number=supervisor_number,
                    agent_phone=context.recipient_phone,
                    customer_phone=context.sender_phone,
                    customer_name=customer_name,
                    products=proof_products,
                    proof_presigned_url=presigned_url,
                    lead_info=context.lead_info,
                )

            # 5. Telegram native upload — bytes go directly, no expiry problem
            if settings.telegram_bot_token and settings.telegram_chat_id:
                # Cart wins; relevant_products is only the inventory match (with stock counts as quantity).
                products = context.cart_contents.get("items") or context.relevant_products
                total = sum(
                    float(p.get("price", 0)) * int(p.get("quantity", 1)) for p in products
                )
                sale_data = {
                    "phone": context.sender_phone,
                    "items": [
                        {"name": p.get("name") or p.get("product_name", "Producto"), "quantity": p.get("quantity", 1)}
                        for p in products
                    ],
                    "total_price": f"${total:.2f}",
                    "checkout_data": context.checkout_data,
                    "conversation_id": str(context.conversation_id or ""),
                }
                await telegram_service.send_payment_approval_request(
                    chat_id=settings.telegram_chat_id,
                    sale_data=sale_data,
                    receipt_bytes=file_bytes,
                    content_type=content_type,
                )

            # Mark proof as submitted — keep AWAITING_RECEIPT so the Telegram
            # approve button can still transition to ORDER_COMPLETED.
            context.cart_contents["payment_proof_submitted"] = True
            context.action_result = {
                "lead_id": lead_id,
                "action": "payment_proof_forwarded",
                "supervisor_notified": bool(supervisor_number),
                "receipt_archived": True,
                "receipt_object_key": object_key,
                "proof_media_count": len(context.media_urls),
            }

            self.log_info(
                "payment_proof_processed",
                lead_id=lead_id,
                supervisor=supervisor_number,
                customer=context.sender_phone,
            )

        except Exception as e:
            self.log_error(
                "failed_to_process_payment_proof",
                lead_id=context.lead_id,
                error=str(e),
            )
            context.action_result = {"error": str(e), "action": "payment_proof_processing_failed"}

    async def _save_receipt_object_key(self, lead_id: Optional[int], object_key: str) -> None:
        """Persist the R2 object key of the archived receipt on the lead record."""
        if not lead_id:
            return
        try:
            session_factory = get_session_factory()
            async with session_factory() as db:
                stmt = (
                    update(Lead)
                    .where(Lead.id == lead_id)
                    .values(receipt_object_key=object_key)
                )
                await db.execute(stmt)
                await db.commit()
        except Exception as e:
            self.log_error("failed_to_save_receipt_object_key", lead_id=lead_id, error=str(e))
    
    async def _handle_general_question(self, context: PipelineContext):
        """Handle general question."""
        context.action_type = "general_question"
        context.action_result = {}
