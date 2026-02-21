"""
Pipeline runner that orchestrates all processing stages.
"""

from typing import List
from datetime import datetime
import structlog

from app.schemas.pipeline import PipelineContext, PipelineResult, PipelineStage as PipelineStageEnum
from app.services.pipeline.base import BasePipelineStage, PipelineStageError

# Import pipeline stages (will be implemented later)
from app.services.pipeline.stages.validator import ValidationStage
from app.services.pipeline.stages.identifier import IdentificationStage
from app.services.pipeline.stages.classifier import ClassificationStage
from app.services.pipeline.stages.context_builder import ContextBuilderStage
from app.services.pipeline.stages.action_executor import ActionExecutorStage
from app.services.pipeline.stages.response_generator import ResponseGeneratorStage

logger = structlog.get_logger()


class PipelineRunner:
    """
    Orchestrates the message processing pipeline.
    
    The pipeline consists of 6 sequential stages:
    1. Validation - Validate message format and content
    2. Identification - Identify tenant, agent instance, conversation
    3. Classification - Classify user intent using AI
    4. Context Building - Build conversation context with history and data
    5. Action Execution - Execute appropriate action based on intent
    6. Response Generation - Generate and send response to user
    """
    
    def __init__(self):
        self.logger = logger.bind(component="PipelineRunner")
        
        # Initialize all pipeline stages in order
        self.stages: List[BasePipelineStage] = [
            ValidationStage(),
            IdentificationStage(),
            ClassificationStage(),
            ContextBuilderStage(),
            ActionExecutorStage(),
            ResponseGeneratorStage(),
        ]
    
    async def run(self, context: PipelineContext) -> PipelineResult:
        """
        Run the message through all pipeline stages.
        
        Args:
            context: Initial pipeline context
        
        Returns:
            PipelineResult with processing outcome
        """
        start_time = datetime.utcnow()
        
        self.logger.info(
            "pipeline_started",
            message_sid=context.message_sid,
            sender=context.sender_phone,
            stages_count=len(self.stages)
        )
        
        try:
            # Execute each stage sequentially
            for stage in self.stages:
                # Update current stage in context
                # Map class names to stage enum values
                stage_class_name = stage.__class__.__name__
                stage_mapping = {
                    "ValidationStage": PipelineStageEnum.VALIDATION,
                    "IdentificationStage": PipelineStageEnum.IDENTIFICATION,
                    "ClassificationStage": PipelineStageEnum.CLASSIFICATION,
                    "ContextBuilderStage": PipelineStageEnum.CONTEXT_BUILDING,
                    "ActionExecutorStage": PipelineStageEnum.ACTION_EXECUTION,
                    "ResponseGeneratorStage": PipelineStageEnum.RESPONSE_GENERATION,
                }
                context.current_stage = stage_mapping.get(stage_class_name)
                
                # Execute the stage
                context = await stage.execute(context)
                
                # Check if pipeline should stop early
                if context.error:
                    self.logger.warning(
                        "pipeline_stopped_early",
                        message_sid=context.message_sid,
                        stage=stage.stage_name,
                        error=context.error
                    )
                    break
            
            # Calculate processing time
            processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            
            # Build result
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
    
    async def run_single_stage(
        self,
        stage: BasePipelineStage,
        context: PipelineContext
    ) -> PipelineContext:
        """
        Run a single pipeline stage (useful for testing).
        
        Args:
            stage: Pipeline stage to execute
            context: Pipeline context
        
        Returns:
            Modified context
        """
        return await stage.execute(context)
