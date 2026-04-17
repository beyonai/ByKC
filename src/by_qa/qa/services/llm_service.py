"""LLM service for QA modules using an OpenAI-compatible API."""

from typing import Any, AsyncGenerator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from by_qa.core.exceptions import LLMGenerationError
from by_qa.core.model_config import EnvModelConfigProvider, ModelConfigProvider


class LLMService:
    """Service for LLM interactions."""

    def __init__(self, provider: ModelConfigProvider | None = None):
        self._provider = provider or EnvModelConfigProvider()

    async def _get_model(
        self, model_type: str = "retrieval", streaming: bool = False
    ) -> ChatOpenAI:
        config = await self._provider.get_config(model_type)
        return ChatOpenAI(
            model=config.model_name,
            temperature=config.temperature,
            base_url=config.base_url,
            api_key=config.api_key,
            streaming=streaming,
        )

    async def _get_streaming_model(self, model_type: str = "generator") -> ChatOpenAI:
        return await self._get_model(model_type=model_type, streaming=True)

    async def get_model_config(self, model_type: str):
        """Return the ModelConfig for the given model role."""
        return await self._provider.get_config(model_type)

    def _normalize_messages(
        self, messages: list[dict[str, str] | BaseMessage]
    ) -> list[BaseMessage]:
        normalized: list[BaseMessage] = []
        for msg in messages:
            if isinstance(msg, BaseMessage):
                normalized.append(msg)
                continue
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                normalized.append(SystemMessage(content=content))
            elif role == "assistant":
                normalized.append(AIMessage(content=content))
            else:
                normalized.append(HumanMessage(content=content))
        return normalized

    async def generate(
        self,
        messages: list[dict[str, str] | BaseMessage],
        model_type: str = "generator",
        json_mode: bool = False,
    ) -> str:
        try:
            model = await self._get_streaming_model(model_type=model_type)
            normalized = self._normalize_messages(messages)
            if json_mode:
                response = await model.ainvoke(
                    normalized, response_format={"type": "json_object"}
                )
            else:
                response = await model.ainvoke(normalized)
            return response.content
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise LLMGenerationError(
                message="LLM generation failed",
                details={"error": str(exc)},
            ) from exc

    async def generate_stream(
        self,
        messages: list[dict[str, str] | BaseMessage],
        model_type: str = "generator",
    ) -> AsyncGenerator[str, None]:
        try:
            model = await self._get_streaming_model(model_type=model_type)
            normalized = self._normalize_messages(messages)
            async for chunk in model.astream(normalized):
                if chunk.content:
                    yield chunk.content
        except Exception as exc:  # pylint: disable=broad-exception-caught
            yield f"Error: {str(exc)}"

    async def astream(
        self,
        messages: list[dict[str, str] | BaseMessage],
        model_type: str = "generator",
    ) -> AsyncGenerator[str, None]:
        async for chunk in self.generate_stream(messages, model_type):
            yield chunk

    async def bind_tools(self, tools: list[Any], model_type: str = "retrieval") -> Any:
        model = await self._get_streaming_model(model_type=model_type)
        return model.bind_tools(tools)

    async def ainvoke(self, messages: list[Any], model_type: str = "retrieval") -> Any:
        model = await self._get_streaming_model(model_type=model_type)
        normalized = self._normalize_messages(messages)
        return await model.ainvoke(normalized)

    async def check_health(self) -> dict[str, Any]:
        """Check LLM service health."""
        try:
            model = await self._get_model("classifier")
            await model.ainvoke([HumanMessage(content="Hi")])
            config = await self._provider.get_config("classifier")
            return {
                "status": "healthy",
                "model": config.model_name,
                "response_time": "ok",
            }
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return {"status": "unhealthy", "error": str(exc)}
