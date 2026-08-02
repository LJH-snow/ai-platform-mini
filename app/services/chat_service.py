import logging
from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends

from app.core.container import provide_llm_provider
from app.exceptions.ollama import OllamaServiceError
from app.providers.base import LLMProvider
from app.providers.results import ProviderChatResult
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse, ChatRole

logger = logging.getLogger(__name__)

_VALID_ROLES = {"system", "user", "assistant"}


class ChatService:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def chat(self, request: ChatRequest) -> ChatResponse:
        messages = self._build_messages(request)
        payload: dict[str, object] = {
            "model": request.model or self._provider.default_model,
            "messages": messages,
            "stream": False,
        }
        options = self._build_options(request)
        if options:
            payload["options"] = options
        data = await self._provider.chat(payload)
        result = self._parse_chat_response(data)
        return ChatResponse(
            model=result.model,
            created_at=result.created_at,
            message=ChatMessage(role=result.role, content=result.content),
            done=result.done,
            done_reason=result.done_reason,
        )

    async def chat_stream(
        self, request: ChatRequest
    ) -> AsyncIterator[ProviderChatResult]:
        messages = self._build_messages(request)
        payload: dict[str, object] = {
            "model": request.model or self._provider.default_model,
            "messages": messages,
        }
        options = self._build_options(request)
        if options:
            payload["options"] = options
        async for chunk in self._provider.chat_stream(payload):
            result = self._parse_stream_chunk(chunk)
            if result is not None:
                yield result

    def _build_messages(self, request: ChatRequest) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []

        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})

        messages.extend(
            {"role": message.role, "content": message.content}
            for message in request.history
        )
        messages.append({"role": "user", "content": request.message})
        return messages

    def _build_options(self, request: ChatRequest) -> dict[str, object]:
        options: dict[str, object] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        return options

    def _parse_chat_response(self, data: dict[str, object]) -> ProviderChatResult:
        model = data.get("model")
        message = data.get("message")
        done = data.get("done")
        created_at = data.get("created_at")
        done_reason = data.get("done_reason")

        if not isinstance(model, str):
            raise OllamaServiceError("Ollama response did not include a valid model.")
        if not isinstance(done, bool):
            raise OllamaServiceError(
                "Ollama response did not include a valid done flag."
            )
        if not isinstance(message, dict):
            raise OllamaServiceError("Ollama response did not include a valid message.")

        role = message.get("role")
        content = message.get("content")

        if role not in _VALID_ROLES or not isinstance(content, str):
            raise OllamaServiceError("Ollama returned an invalid assistant message.")
        if created_at is not None and not isinstance(created_at, str):
            raise OllamaServiceError("Ollama returned an invalid created_at value.")
        if done_reason is not None and not isinstance(done_reason, str):
            raise OllamaServiceError("Ollama returned an invalid done_reason value.")

        return ProviderChatResult(
            model=model,
            created_at=created_at,
            role=role,
            content=content,
            done=done,
            done_reason=done_reason,
        )

    def _parse_stream_chunk(self, data: dict[str, object]) -> ProviderChatResult | None:
        model = data.get("model")
        message = data.get("message")
        done = data.get("done")
        created_at = data.get("created_at")
        done_reason = data.get("done_reason")

        if not isinstance(model, str) or not isinstance(done, bool):
            return None
        if not isinstance(message, dict):
            return None

        role = message.get("role")
        content = message.get("content")

        if not isinstance(role, str) or not isinstance(content, str):
            return None

        safe_role: ChatRole = (
            cast(ChatRole, role) if role in _VALID_ROLES else "assistant"
        )
        return ProviderChatResult(
            model=model,
            created_at=created_at if isinstance(created_at, str) else None,
            role=safe_role,
            content=content,
            done=done,
            done_reason=done_reason if isinstance(done_reason, str) else None,
        )


def get_chat_service(
    provider: Annotated[LLMProvider, Depends(provide_llm_provider)],
) -> ChatService:
    return ChatService(provider)
