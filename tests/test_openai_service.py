import pytest

from app.core.context import RequestContext
from app.providers.mock import MockProvider
from app.schemas.openai import OpenAIChatMessage, OpenAIChatRequest
from app.services.chat_service import ChatService
from app.services.openai_service import OpenAIService
from app.usage.collector import UsageCollector
from app.usage.service import UsageService

_test_context = RequestContext(request_id="test-req")


@pytest.fixture
def openai_service() -> OpenAIService:
    usage_service = UsageService()
    return OpenAIService(
        chat_service=ChatService(provider=MockProvider()),
        usage_collector=UsageCollector(usage_service),
    )


@pytest.mark.asyncio
async def test_non_stream_completion(openai_service: OpenAIService) -> None:
    request = OpenAIChatRequest(
        messages=[OpenAIChatMessage(role="user", content="Hi")],
    )
    response = await openai_service.chat_completions(request, context=_test_context)

    assert response.object == "chat.completion"
    assert response.model == "mock-model"
    assert len(response.choices) == 1
    assert response.choices[0].message.role == "assistant"
    assert response.choices[0].message.content == "Hello from Mock Provider"
    assert response.choices[0].finish_reason == "stop"


@pytest.mark.asyncio
async def test_non_stream_passes_model(openai_service: OpenAIService) -> None:
    request = OpenAIChatRequest(
        model="custom-model",
        messages=[OpenAIChatMessage(role="user", content="Hi")],
    )
    response = await openai_service.chat_completions(request, context=_test_context)
    assert response.model == "custom-model"


@pytest.mark.asyncio
async def test_non_stream_extracts_system_prompt(
    openai_service: OpenAIService,
) -> None:
    request = OpenAIChatRequest(
        messages=[
            OpenAIChatMessage(role="system", content="Be brief."),
            OpenAIChatMessage(role="user", content="Hi"),
        ],
    )
    response = await openai_service.chat_completions(request, context=_test_context)
    assert response.choices[0].message.role == "assistant"


@pytest.mark.asyncio
async def test_stream_yields_sse_chunks(openai_service: OpenAIService) -> None:
    request = OpenAIChatRequest(
        messages=[OpenAIChatMessage(role="user", content="Hi")],
        stream=True,
    )
    chunks = [
        chunk
        async for chunk in openai_service.chat_completions_stream(
            request, context=_test_context
        )
    ]

    assert chunks[0].startswith("data: ")
    assert chunks[-1] == "data: [DONE]\n\n"

    import json

    first_data = json.loads(chunks[0][6:].strip())
    assert first_data["object"] == "chat.completion.chunk"
    assert first_data["choices"][0]["delta"]["role"] == "assistant"

    second_data = json.loads(chunks[1][6:].strip())
    assert "content" in second_data["choices"][0]["delta"]


@pytest.mark.asyncio
async def test_stream_finish_reason(openai_service: OpenAIService) -> None:
    request = OpenAIChatRequest(
        messages=[OpenAIChatMessage(role="user", content="Hi")],
        stream=True,
    )
    chunks = [
        chunk
        async for chunk in openai_service.chat_completions_stream(
            request, context=_test_context
        )
    ]

    import json

    last_data = json.loads(chunks[-2][6:].strip())
    assert last_data["choices"][0]["finish_reason"] == "stop"
