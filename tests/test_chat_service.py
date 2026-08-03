from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.providers.mock import MockProvider
from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService


@pytest.fixture
def service() -> ChatService:
    return ChatService(provider=MockProvider())


class _BooleanTokenProvider(MockProvider):
    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await super().chat(payload)
        response["prompt_eval_count"] = True
        response["eval_count"] = False
        return response

    async def chat_stream(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        async for chunk in super().chat_stream(payload):
            chunk["prompt_eval_count"] = True
            chunk["eval_count"] = False
            yield chunk


@pytest.mark.asyncio
async def test_chat_returns_mock_response(service: ChatService) -> None:
    request = ChatRequest(message="Hi")
    response = await service.chat(request)

    assert response.model == "mock-model"
    assert response.message.role == "assistant"
    assert response.message.content == "Hello from Mock Provider"
    assert response.done is True
    assert response.done_reason == "stop"


@pytest.mark.asyncio
async def test_chat_passes_options(service: ChatService) -> None:
    request = ChatRequest(message="Hi", temperature=0.5, max_tokens=100)
    response = await service.chat(request)

    assert response.model == "mock-model"


@pytest.mark.asyncio
async def test_chat_stream_yields_tokens(service: ChatService) -> None:
    request = ChatRequest(message="Hi")
    chunks = [chunk async for chunk in service.chat_stream(request)]

    assert len(chunks) == 5
    assert chunks[0].content == "Hello "
    assert chunks[3].content == "Provider"
    assert chunks[4].done is True
    assert chunks[4].done_reason == "stop"


@pytest.mark.asyncio
async def test_chat_stream_with_history(service: ChatService) -> None:
    from app.schemas.chat import ChatMessage

    request = ChatRequest(
        message="Follow up",
        history=[ChatMessage(role="user", content="First message")],
    )
    chunks = [chunk async for chunk in service.chat_stream(request)]
    assert len(chunks) == 5


@pytest.mark.asyncio
async def test_chat_rejects_boolean_token_counts() -> None:
    service = ChatService(provider=_BooleanTokenProvider())

    response = await service.chat(ChatRequest(message="Hi"))

    assert response.prompt_tokens is None
    assert response.completion_tokens is None


@pytest.mark.asyncio
async def test_chat_stream_rejects_boolean_token_counts() -> None:
    service = ChatService(provider=_BooleanTokenProvider())

    chunks = [chunk async for chunk in service.chat_stream(ChatRequest(message="Hi"))]

    assert chunks
    assert all(chunk.prompt_tokens is None for chunk in chunks)
    assert all(chunk.completion_tokens is None for chunk in chunks)
