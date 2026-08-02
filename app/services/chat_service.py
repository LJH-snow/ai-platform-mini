import logging

from app.exceptions.ollama import OllamaServiceError
from app.providers.ollama import OllamaProvider, get_ollama_provider
from app.providers.results import ProviderChatResult
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

_VALID_ROLES = {"system", "user", "assistant"}


class ChatService:
    def __init__(self, provider: OllamaProvider) -> None:
        self._provider = provider

    async def chat(self, request: ChatRequest) -> ChatResponse:
        messages = self._build_messages(request)
        payload = {
            "model": request.model or self._provider.default_model,
            "messages": messages,
            "stream": False,
        }
        data = await self._provider.chat(payload)
        result = self._parse_chat_response(data)
        return ChatResponse(
            model=result.model,
            created_at=result.created_at,
            message=ChatMessage(role=result.role, content=result.content),
            done=result.done,
            done_reason=result.done_reason,
        )

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


def get_chat_service() -> ChatService:
    return ChatService(provider=get_ollama_provider())
