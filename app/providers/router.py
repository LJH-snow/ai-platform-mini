import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class ProviderRouter:
    """Route provider-neutral requests by their requested model name."""

    def __init__(
        self,
        default_provider: LLMProvider,
        openai_provider: LLMProvider,
    ) -> None:
        self._default_provider = default_provider
        self._openai_provider = openai_provider

    @property
    def default_model(self) -> str:
        return self._default_provider.default_model

    def route_provider(self, model: str) -> LLMProvider:
        if model == self.default_model:
            return self._default_provider
        if model.startswith("gpt-"):
            return self._openai_provider
        return self._default_provider

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider = self._provider_for_payload(payload)
        return await provider.chat(payload)

    async def chat_stream(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        provider = self._provider_for_payload(payload)
        async for chunk in provider.chat_stream(payload):
            yield chunk

    async def list_models(self) -> dict[str, Any]:
        return await self._default_provider.list_models()

    async def close(self) -> None:
        errors: list[Exception] = []
        external_cancel: asyncio.CancelledError | None = None
        providers = [self._default_provider]
        if self._openai_provider is not self._default_provider:
            providers.append(self._openai_provider)

        for provider in providers:
            try:
                await provider.close()
            except asyncio.CancelledError as exc:
                current = asyncio.current_task()
                if current is not None and current.cancelling() > 0:
                    external_cancel = exc
                else:
                    errors.append(RuntimeError(f"Provider close cancelled: {exc}"))
            except Exception as exc:
                errors.append(exc)

        if external_cancel is not None:
            if errors:
                logger.error(
                    "Provider close errors suppressed by external cancellation: %s",
                    errors,
                )
            raise external_cancel

        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise ExceptionGroup("Failed to close LLM providers.", errors)

    def _provider_for_payload(self, payload: dict[str, Any]) -> LLMProvider:
        model = payload.get("model")
        if not isinstance(model, str):
            model = self.default_model
        return self.route_provider(model)
