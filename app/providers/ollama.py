import logging
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
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    @property
    def default_model(self) -> str:
        return self._default_model

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/chat", payload=payload)

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
            if self._http_client is not None:
                response = await self._http_client.request(method, path, json=payload)
            else:
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=self._timeout_seconds,
                ) as client:
                    response = await client.request(method, path, json=payload)
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
