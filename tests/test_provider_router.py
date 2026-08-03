import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.providers.base import LLMProvider
from app.providers.router import ProviderRouter
from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService


class RecordingProvider:
    def __init__(self, default_model: str) -> None:
        self._default_model = default_model
        self.chat_payloads: list[dict[str, Any]] = []
        self.stream_payloads: list[dict[str, Any]] = []
        self.close_count = 0

    @property
    def default_model(self) -> str:
        return self._default_model

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.chat_payloads.append(payload)
        model = payload.get("model")
        return {
            "model": model if isinstance(model, str) else self._default_model,
            "created_at": "2026-08-03T00:00:00Z",
            "message": {"role": "assistant", "content": self._default_model},
            "done": True,
            "done_reason": "stop",
        }

    async def chat_stream(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        self.stream_payloads.append(payload)
        model = payload.get("model")
        yield {
            "model": model if isinstance(model, str) else self._default_model,
            "created_at": "2026-08-03T00:00:00Z",
            "message": {"role": "assistant", "content": self._default_model},
            "done": True,
            "done_reason": "stop",
        }

    async def list_models(self) -> dict[str, Any]:
        return {"models": [{"name": self._default_model}]}

    async def close(self) -> None:
        self.close_count += 1


class FailingCloseProvider(RecordingProvider):
    def __init__(self, default_model: str, error_message: str = "close failed") -> None:
        super().__init__(default_model)
        self._error_message = error_message

    async def close(self) -> None:
        await super().close()
        raise RuntimeError(self._error_message)


class CancelledCloseProvider(RecordingProvider):
    async def close(self) -> None:
        await super().close()
        raise asyncio.CancelledError


def make_router() -> tuple[ProviderRouter, RecordingProvider, RecordingProvider]:
    default_provider = RecordingProvider("qwen3:4b")
    openai_provider = RecordingProvider("gpt-4.1-mini")
    return (
        ProviderRouter(
            default_provider=default_provider,
            openai_provider=openai_provider,
        ),
        default_provider,
        openai_provider,
    )


def test_route_provider_uses_gpt_prefix_for_openai() -> None:
    router, default_provider, openai_provider = make_router()

    assert router.route_provider("gpt-4.1-mini") is openai_provider
    assert router.route_provider("gpt-custom") is openai_provider
    assert router.route_provider("qwen3:4b") is default_provider
    assert router.route_provider("claude-3") is default_provider


def test_default_model_takes_priority_over_gpt_prefix() -> None:
    default_provider = RecordingProvider("gpt-local")
    openai_provider = RecordingProvider("gpt-4.1-mini")
    router = ProviderRouter(
        default_provider=default_provider,
        openai_provider=openai_provider,
    )

    assert router.route_provider("gpt-local") is default_provider


@pytest.mark.asyncio
async def test_chat_routes_by_requested_model() -> None:
    router, default_provider, openai_provider = make_router()

    await router.chat({"model": "gpt-4.1-mini"})
    await router.chat({"model": "qwen3:4b"})

    assert [payload["model"] for payload in openai_provider.chat_payloads] == [
        "gpt-4.1-mini"
    ]
    assert [payload["model"] for payload in default_provider.chat_payloads] == [
        "qwen3:4b"
    ]


@pytest.mark.asyncio
async def test_chat_without_model_uses_default_provider() -> None:
    router, default_provider, openai_provider = make_router()

    await router.chat({"messages": []})

    assert len(default_provider.chat_payloads) == 1
    assert not openai_provider.chat_payloads


@pytest.mark.asyncio
async def test_chat_service_without_model_uses_gpt_named_default_provider() -> None:
    default_provider = RecordingProvider("gpt-local")
    openai_provider = RecordingProvider("gpt-4.1-mini")
    service = ChatService(
        ProviderRouter(
            default_provider=default_provider,
            openai_provider=openai_provider,
        )
    )

    response = await service.chat(ChatRequest(message="Hello"))

    assert response.model == "gpt-local"
    assert response.message.content == "gpt-local"
    assert len(default_provider.chat_payloads) == 1
    assert not openai_provider.chat_payloads


@pytest.mark.asyncio
async def test_chat_stream_routes_by_requested_model() -> None:
    router, default_provider, openai_provider = make_router()

    openai_chunks = [
        chunk async for chunk in router.chat_stream({"model": "gpt-4.1-mini"})
    ]
    ollama_chunks = [chunk async for chunk in router.chat_stream({"model": "qwen3:4b"})]

    assert openai_chunks[0]["message"]["content"] == "gpt-4.1-mini"
    assert ollama_chunks[0]["message"]["content"] == "qwen3:4b"
    assert len(openai_provider.stream_payloads) == 1
    assert len(default_provider.stream_payloads) == 1


@pytest.mark.asyncio
async def test_chat_service_stream_defaults_to_gpt_named_provider() -> None:
    default_provider = RecordingProvider("gpt-local")
    openai_provider = RecordingProvider("gpt-4.1-mini")
    service = ChatService(
        ProviderRouter(
            default_provider=default_provider,
            openai_provider=openai_provider,
        )
    )

    chunks = [
        chunk async for chunk in service.chat_stream(ChatRequest(message="Hello"))
    ]

    assert len(chunks) == 1
    assert chunks[0].model == "gpt-local"
    assert chunks[0].content == "gpt-local"
    assert chunks[0].done is True
    assert len(default_provider.stream_payloads) == 1
    assert not openai_provider.stream_payloads


@pytest.mark.asyncio
async def test_list_models_uses_default_provider() -> None:
    router, default_provider, openai_provider = make_router()

    result = await router.list_models()

    assert result == {"models": [{"name": "qwen3:4b"}]}
    assert default_provider.close_count == 0
    assert openai_provider.close_count == 0


@pytest.mark.asyncio
async def test_close_closes_each_provider_once() -> None:
    router, default_provider, openai_provider = make_router()

    await router.close()

    assert default_provider.close_count == 1
    assert openai_provider.close_count == 1


@pytest.mark.asyncio
async def test_close_closes_shared_provider_once() -> None:
    provider = RecordingProvider("qwen3:4b")
    router = ProviderRouter(
        default_provider=provider,
        openai_provider=provider,
    )

    await router.close()

    assert provider.close_count == 1


@pytest.mark.asyncio
async def test_close_attempts_all_providers_when_one_fails() -> None:
    default_provider = FailingCloseProvider("qwen3:4b")
    openai_provider = RecordingProvider("gpt-4.1-mini")
    router = ProviderRouter(
        default_provider=default_provider,
        openai_provider=openai_provider,
    )

    with pytest.raises(RuntimeError, match="close failed"):
        await router.close()

    assert default_provider.close_count == 1
    assert openai_provider.close_count == 1


@pytest.mark.asyncio
async def test_close_attempts_all_providers_before_reraising_cancellation() -> None:
    default_provider = CancelledCloseProvider("qwen3:4b")
    openai_provider = RecordingProvider("gpt-4.1-mini")
    router = ProviderRouter(
        default_provider=default_provider,
        openai_provider=openai_provider,
    )

    with pytest.raises(asyncio.CancelledError):
        await router.close()

    assert default_provider.close_count == 1
    assert openai_provider.close_count == 1


@pytest.mark.asyncio
async def test_close_preserves_cancellation_and_provider_failure() -> None:
    default_provider = CancelledCloseProvider("qwen3:4b")
    openai_provider = FailingCloseProvider(
        "gpt-4.1-mini",
        "openai close failed",
    )
    router = ProviderRouter(
        default_provider=default_provider,
        openai_provider=openai_provider,
    )

    with pytest.raises(BaseExceptionGroup) as exc_info:
        await router.close()

    errors = exc_info.value.exceptions
    assert len(errors) == 2
    assert isinstance(errors[0], asyncio.CancelledError)
    assert isinstance(errors[1], RuntimeError)
    assert str(errors[1]) == "openai close failed"
    assert default_provider.close_count == 1
    assert openai_provider.close_count == 1


@pytest.mark.asyncio
async def test_close_reports_all_provider_failures() -> None:
    default_provider = FailingCloseProvider("qwen3:4b", "ollama close failed")
    openai_provider = FailingCloseProvider(
        "gpt-4.1-mini",
        "openai close failed",
    )
    router = ProviderRouter(
        default_provider=default_provider,
        openai_provider=openai_provider,
    )

    with pytest.raises(ExceptionGroup) as exc_info:
        await router.close()

    assert [str(error) for error in exc_info.value.exceptions] == [
        "ollama close failed",
        "openai close failed",
    ]
    assert default_provider.close_count == 1
    assert openai_provider.close_count == 1


def test_router_satisfies_provider_protocol() -> None:
    router, _, _ = make_router()

    assert isinstance(router, LLMProvider)
