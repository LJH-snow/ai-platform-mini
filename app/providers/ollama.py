import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.settings import get_settings
from app.exceptions.ollama import OllamaModelNotFoundError, OllamaServiceError

logger = logging.getLogger(__name__)


class OllamaProvider:
    def __init__(
        self,
        base_url: str,
        default_model: str,
        timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
        )

    @property
    def default_model(self) -> str:
        return self._default_model

    async def close(self) -> None:
        await self._client.aclose()

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/chat", payload=payload)

    async def chat_stream(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        stream_payload = {**payload, "stream": True}
        try:
            async with self._client.stream(
                "POST", "/api/chat", json=stream_payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(data, dict):
                        yield data
        except httpx.HTTPStatusError as exc:
            raise OllamaServiceError(
                f"Ollama streaming request failed with status "
                f"{exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise OllamaServiceError(
                f"Unable to reach Ollama at {self._base_url}. "
                "Check that the Ollama service is running."
            ) from exc

    async def list_models(self) -> dict[str, Any]:
        return await self._request("GET", "/api/tags")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=payload)
        except httpx.RequestError as exc:
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

        if response.status_code == httpx.codes.NOT_FOUND:
            error_message = data.get("error")
            if isinstance(error_message, str):
                raise OllamaModelNotFoundError(error_message)
            raise OllamaModelNotFoundError("Requested Ollama model was not found.")

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error_message = data.get("error")
            if isinstance(error_message, str):
                raise OllamaServiceError(error_message) from exc
            raise OllamaServiceError(
                f"Ollama request failed with status {response.status_code}."
            ) from exc

        return data


def get_ollama_provider() -> OllamaProvider:
    settings = get_settings()
    return OllamaProvider(
        base_url=settings.ollama_base_url,
        default_model=settings.ollama_default_model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )
