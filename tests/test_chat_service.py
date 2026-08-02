import pytest

from app.providers.mock import MockProvider
from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService


@pytest.fixture
def service() -> ChatService:
    return ChatService(provider=MockProvider())


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
