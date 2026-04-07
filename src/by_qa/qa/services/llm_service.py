"""LLM service for QA modules using an OpenAI-compatible API."""

from typing import Any, AsyncGenerator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from by_qa.config import get_settings


class LLMService:
    """Service for LLM interactions."""

    def __init__(self):
        self.settings = get_settings()

    def _get_model(
        self, model_type: str = "retrieval", streaming: bool = False
    ) -> ChatOpenAI:
        model_map = {
            "classifier": (
                self.settings.classifier_model,
                self.settings.classifier_temp,
            ),
            "retrieval": (self.settings.retrieval_model, self.settings.retrieval_temp),
            "generator": (self.settings.generator_model, self.settings.generator_temp),
            "quality": (self.settings.quality_model, self.settings.quality_temp),
        }
        model_name, temperature = model_map.get(model_type, model_map["generator"])
        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            base_url=self.settings.llm_base_url,
            api_key=self.settings.llm_api_key,
            streaming=streaming,
        )

    def _get_streaming_model(self, model_type: str = "generator") -> ChatOpenAI:
        """Get a configured streaming model."""
        return self._get_model(model_type=model_type, streaming=True)

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
            model = self._get_streaming_model(model_type=model_type)
            normalized = self._normalize_messages(messages)
            if json_mode:
                response = await model.ainvoke(
                    normalized, response_format={"type": "json_object"}
                )
            else:
                response = await model.ainvoke(normalized)
            return response.content
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return (
                f'{{"error": "LLM generation failed: {str(exc)}"}}'
                if json_mode
                else f"Error: {str(exc)}"
            )

    async def generate_stream(
        self,
        messages: list[dict[str, str] | BaseMessage],
        model_type: str = "generator",
    ) -> AsyncGenerator[str, None]:
        try:
            model = self._get_streaming_model(model_type=model_type)
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

    def bind_tools(self, tools: list[Any], model_type: str = "retrieval") -> Any:
        model = self._get_streaming_model(model_type=model_type)
        return model.bind_tools(tools)

    async def ainvoke(self, messages: list[Any], model_type: str = "retrieval") -> Any:
        model = self._get_streaming_model(model_type=model_type)
        normalized = self._normalize_messages(messages)
        return await model.ainvoke(normalized)

    async def check_health(self) -> dict[str, Any]:
        """Check LLM service health."""
        try:
            model = self._get_model("classifier")
            await model.ainvoke([HumanMessage(content="Hi")])
            return {
                "status": "healthy",
                "model": self.settings.classifier_model,
                "response_time": "ok",
            }
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return {"status": "unhealthy", "error": str(exc)}


_llm_service: LLMService | None = None


def get_llm_service() -> LLMService:
    """Get or create the QA-scoped LLM service."""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
