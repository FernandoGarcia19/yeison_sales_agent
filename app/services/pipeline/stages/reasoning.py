from langchain_openai import ChatOpenAI
from app.services.pipeline.stages.tools import AVAILABLE_TOOLS
from app.core.config import settings

class ReasoningNode:
    """
    Example ReasoningNode that binds the Langchain tools.
    """
    def __init__(self):
        # Initialize the LLM
        self.llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=settings.openai_temperature
        )
        
        # Minor update: Bind the tools to the LLM
        self.llm_with_tools = self.llm.bind_tools(AVAILABLE_TOOLS)

    async def __call__(self, state: dict):
        """
        Executes the reasoning step and determines the next tools to call or the final response.
        """
        messages = state.get("messages", [])
        
        # Invoke the LLM with the bound tools
        response = await self.llm_with_tools.ainvoke(messages)
        
        return {"messages": [response]}
