from typing import Any

import httpx

from app.core.config import get_settings
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse

_VALID_ROLES = {"system", "user", "assistant"}


class OllamaServiceError(Exception):
    """Raised when the Ollama API cannot satisfy a request."""


class OllamaService:
    def __init__(
        self,
        base_url: str,
        default_model: str,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    async def chat(self, request: ChatRequest) -> ChatResponse:
        payload = {
            "model": request.model or self._default_model,
            "messages": self._build_messages(request),
            "stream": False,
        }
        data = await self._post_json("/api/chat", payload)
        return self._parse_chat_response(data)

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

    async def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            if self._http_client is not None:
                response = await self._http_client.post(path, json=payload)
            else:
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=self._timeout_seconds,
                ) as client:
                    response = await client.post(path, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaServiceError(
                f"Unable to reach Ollama at {self._base_url}. "
                "Check that the Ollama service is running."
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaServiceError("Ollama returned invalid JSON.") from exc

        if not isinstance(data, dict):
            raise OllamaServiceError("Ollama returned an unexpected response shape.")

        return data

    def _parse_chat_response(self, data: dict[str, Any]) -> ChatResponse:
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

        return ChatResponse(
            model=model,
            created_at=created_at,
            message=ChatMessage(role=role, content=content),
            done=done,
            done_reason=done_reason,
        )


def get_ollama_service() -> OllamaService:
    settings = get_settings()
    return OllamaService(
        base_url=settings.ollama_base_url,
        default_model=settings.ollama_default_model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )
