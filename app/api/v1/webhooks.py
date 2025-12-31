"""
Twilio webhook endpoint for receiving WhatsApp messages.
"""

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks, Depends, Form
from fastapi.responses import Response
import structlog
from datetime import datetime

from app.schemas.webhook import TwilioWebhookRequest, TwilioWebhookResponse
from app.schemas.pipeline import PipelineContext
from app.services.pipeline.runner import PipelineRunner
from app.integrations.whatsapp.validator import validate_twilio_signature

logger = structlog.get_logger()

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/twilio")
async def verify_webhook(request: Request):
    """
    Twilio webhook verification endpoint.
    
    Twilio sends a GET request to verify the webhook URL during setup.
    """
    # You can add custom verification logic here if needed
    return {"status": "webhook verified"}


@router.post("/twilio")
async def receive_twilio_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    # Twilio sends data as form-encoded
    MessageSid: str = Form(...),
    SmsSid: str = Form(...),
    AccountSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(...),
    NumMedia: str = Form(default="0"),
    ProfileName: str = Form(default=None),
    WaId: str = Form(default=None),
    MessagingServiceSid: str = Form(default=None),
    Latitude: str = Form(default=None),
    Longitude: str = Form(default=None),
    MediaUrl0: str = Form(default=None),
    MediaContentType0: str = Form(default=None),
):
    """
    Receive WhatsApp messages from Twilio webhook.
    
    This endpoint:
    1. Validates the Twilio signature
    2. Parses the webhook payload
    3. Queues the message for pipeline processing
    4. Returns 200 OK immediately to Twilio
    
    Processing happens in the background to ensure fast webhook response.
    """
    
    # Build webhook request object
    webhook_data = TwilioWebhookRequest(
        MessageSid=MessageSid,
        SmsSid=SmsSid,
        AccountSid=AccountSid,
        From=From,
        To=To,
        Body=Body,
        NumMedia=NumMedia,
        ProfileName=ProfileName,
        WaId=WaId,
        MessagingServiceSid=MessagingServiceSid,
        Latitude=Latitude,
        Longitude=Longitude,
        MediaUrl0=MediaUrl0,
        MediaContentType0=MediaContentType0,
    )
    
    logger.info(
        "twilio_webhook_received",
        message_sid=webhook_data.MessageSid,
        from_number=webhook_data.sender_phone,
        to_number=webhook_data.recipient_phone,
        has_media=webhook_data.has_media,
    )
    
    # Validate Twilio signature (security check)
    try:
        form_data = await request.form()
        is_valid = await validate_twilio_signature(
            request=request,
            form_data=dict(form_data)
        )
        
        if not is_valid:
            logger.warning(
                "invalid_twilio_signature",
                message_sid=webhook_data.MessageSid
            )
            raise HTTPException(status_code=403, detail="Invalid signature")
    
    except Exception as e:
        logger.error(
            "signature_validation_error",
            error=str(e),
            message_sid=webhook_data.MessageSid
        )
        # In development, you might want to skip validation
        # For production, uncomment the line below:
        # raise HTTPException(status_code=403, detail="Signature validation failed")
    
    # Queue message for background processing
    background_tasks.add_task(
        process_message_pipeline,
        webhook_data=webhook_data
    )
    
    # Return immediate response to Twilio (must respond within 15 seconds)
    return Response(
        content="",
        status_code=200,
        media_type="text/plain"
    )


async def process_message_pipeline(webhook_data: TwilioWebhookRequest):
    """
    Process incoming message through the pipeline.
    
    This runs in the background after the webhook returns 200 OK to Twilio.
    
    Pipeline stages:
    1. Validation - Verify message format
    2. Identification - Identify tenant, agent, conversation
    3. Classification - Classify intent using AI
    4. Context Building - Build conversation context
    5. Action Execution - Execute appropriate action
    6. Response Generation - Generate and send response
    """
    
    start_time = datetime.utcnow()
    
    try:
        # Build pipeline context
        context = PipelineContext(
            message_sid=webhook_data.MessageSid,
            sender_phone=webhook_data.sender_phone,
            recipient_phone=webhook_data.recipient_phone,
            message_body=webhook_data.Body,
            profile_name=webhook_data.ProfileName,
            media_urls=webhook_data.get_media_urls(),
        )
        
        # Run through pipeline
        pipeline_runner = PipelineRunner()
        result = await pipeline_runner.run(context)
        
        # Calculate processing time
        processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000
        
        logger.info(
            "pipeline_completed",
            message_sid=webhook_data.MessageSid,
            success=result.success,
            intent=result.intent,
            action=result.action_executed,
            processing_time_ms=processing_time,
        )
    
    except Exception as e:
        processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000
        
        logger.error(
            "pipeline_failed",
            message_sid=webhook_data.MessageSid,
            error=str(e),
            error_type=type(e).__name__,
            processing_time_ms=processing_time,
        )
        
        # TODO: Send error notification to monitoring system
        # TODO: Send fallback message to user
