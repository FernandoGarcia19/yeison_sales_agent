"""
Base interfaces for the message processing pipeline.
"""

from abc import ABC, abstractmethod
from typing import Optional
import structlog

from app.schemas.pipeline import PipelineContext, PipelineStage

logger = structlog.get_logger()


class BasePipelineStage(ABC):
    """
    Abstract base class for pipeline stages.
    
    Each stage processes the context and returns it (possibly modified).
    Stages are executed sequentially by the PipelineRunner.
    """
    
    def __init__(self):
        self.stage_name = self.__class__.__name__
        self.logger = logger.bind(stage=self.stage_name)
    
    @abstractmethod
    async def process(self, context: PipelineContext) -> PipelineContext:
        """
        Process the pipeline context.
        
        Args:
            context: Current pipeline context
        
        Returns:
            Modified context (or same context if no changes)
        
        Raises:
            Exception: If processing fails
        """
        pass
    
    async def execute(self, context: PipelineContext) -> PipelineContext:
        """
        Execute the stage with logging and error handling.
        
        This method wraps the process() method to add logging.
        """
        self.logger.info(
            "stage_started",
            message_sid=context.message_sid,
            current_stage=context.current_stage
        )
        
        try:
            # Execute the stage
            result_context = await self.process(context)
            
            self.logger.info(
                "stage_completed",
                message_sid=context.message_sid,
                current_stage=context.current_stage
            )
            
            return result_context
        
        except Exception as e:
            self.logger.error(
                "stage_failed",
                message_sid=context.message_sid,
                error=str(e),
                error_type=type(e).__name__
            )
            raise
    
    def log_info(self, message: str, **kwargs):
        """Helper method for logging info."""
        self.logger.info(message, **kwargs)
    
    def log_error(self, message: str, **kwargs):
        """Helper method for logging errors."""
        self.logger.error(message, **kwargs)


class PipelineStageError(Exception):
    """Base exception for pipeline stage errors."""
    
    def __init__(
        self,
        message: str,
        stage: str,
        context: Optional[PipelineContext] = None
    ):
        self.message = message
        self.stage = stage
        self.context = context
        super().__init__(self.message)


class ValidationError(PipelineStageError):
    """Raised when validation fails."""
    pass


class IdentificationError(PipelineStageError):
    """Raised when tenant/agent identification fails."""
    pass


class ClassificationError(PipelineStageError):
    """Raised when intent classification fails."""
    pass


class ActionExecutionError(PipelineStageError):
    """Raised when action execution fails."""
    pass
