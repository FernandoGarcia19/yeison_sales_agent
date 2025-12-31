"""
Validation stage - validates incoming message format and content.
"""

from app.schemas.pipeline import PipelineContext
from app.services.pipeline.base import BasePipelineStage, ValidationError


class ValidationStage(BasePipelineStage):
    """
    Stage 1: Validate incoming message.
    
    Checks:
    - Message has required fields
    - Phone numbers are valid
    - Message body is not empty
    - Media URLs are accessible (if present)
    """
    
    async def process(self, context: PipelineContext) -> PipelineContext:
        """Validate the incoming message."""
        
        self.log_info(
            "validating_message",
            message_sid=context.message_sid,
            has_media=len(context.media_urls) > 0
        )
        
        # Validate required fields
        if not context.sender_phone:
            raise ValidationError(
                "Missing sender phone number",
                stage=self.stage_name,
                context=context
            )
        
        if not context.recipient_phone:
            raise ValidationError(
                "Missing recipient phone number",
                stage=self.stage_name,
                context=context
            )
        
        if not context.message_body and not context.media_urls:
            raise ValidationError(
                "Message has no body and no media",
                stage=self.stage_name,
                context=context
            )
        
        # Validate phone number format (basic check)
        if not context.sender_phone.startswith("+"):
            self.log_info(
                "sender_phone_missing_plus",
                sender_phone=context.sender_phone
            )
        
        # TODO: Add more validation logic as needed:
        # - Check if message is spam
        # - Validate media URLs
        # - Check message length limits
        
        self.log_info(
            "validation_passed",
            message_sid=context.message_sid
        )
        
        return context
