from collections.abc import AsyncIterator

import pytest

from app.core.context import RequestContext
from app.providers.results import ProviderChatResult
from app.schemas.chat import ChatMessage, ChatResponse
from app.usage.collector import UsageCollector
from app.usage.service import UsageService


def _make_context(request_id: str = "test-req") -> RequestContext:
    return RequestContext(request_id=request_id)


def test_record_chat_creates_usage_record() -> None:
    service = UsageService()
    collector = UsageCollector(service)

    response = ChatResponse(
        model="qwen3:4b",
        created_at=None,
        message=ChatMessage(role="assistant", content="hi"),
        done=True,
        done_reason="stop",
        prompt_tokens=10,
        completion_tokens=20,
    )
    collector.record_chat(context=_make_context(), response=response, latency_ms=100.0)

    assert service.record_count == 1
    record = service._records[0]
    assert record.model == "qwen3:4b"
    assert record.prompt_tokens == 10
    assert record.completion_tokens == 20
    assert record.total_tokens == 30
    assert record.latency_ms == 100.0


def test_record_chat_null_tokens_defaults_to_zero() -> None:
    service = UsageService()
    collector = UsageCollector(service)

    response = ChatResponse(
        model="qwen3:4b",
        created_at=None,
        message=ChatMessage(role="assistant", content="hi"),
        done=True,
        done_reason="stop",
        prompt_tokens=None,
        completion_tokens=None,
    )
    collector.record_chat(context=_make_context(), response=response, latency_ms=50.0)

    record = service._records[0]
    assert record.prompt_tokens == 0
    assert record.completion_tokens == 0
    assert record.total_tokens == 0


def test_record_chat_propagates_api_key_name() -> None:
    service = UsageService()
    collector = UsageCollector(service)

    context = RequestContext(request_id="r1", api_key="sk-test", api_key_name="admin")
    response = ChatResponse(
        model="qwen3:4b",
        created_at=None,
        message=ChatMessage(role="assistant", content="hi"),
        done=True,
        done_reason="stop",
        prompt_tokens=5,
        completion_tokens=5,
    )
    collector.record_chat(context=context, response=response, latency_ms=50.0)

    assert service._records[0].api_key_name == "admin"


@pytest.mark.asyncio
async def test_record_stream_captures_final_tokens() -> None:
    service = UsageService()
    collector = UsageCollector(service)

    async def mock_stream() -> AsyncIterator[ProviderChatResult]:
        yield ProviderChatResult(
            model="qwen3:4b",
            created_at=None,
            role="assistant",
            content="hello",
            done=False,
            done_reason=None,
        )
        yield ProviderChatResult(
            model="qwen3:4b",
            created_at=None,
            role="assistant",
            content="",
            done=True,
            done_reason="stop",
            prompt_tokens=10,
            completion_tokens=20,
        )

    results = []
    async for result in collector.record_stream(
        context=_make_context(), stream=mock_stream(), model="qwen3:4b"
    ):
        results.append(result)

    assert len(results) == 2
    assert service.record_count == 1
    record = service._records[0]
    assert record.prompt_tokens == 10
    assert record.completion_tokens == 20
    assert record.total_tokens == 30


@pytest.mark.asyncio
async def test_record_stream_uses_result_model_not_param() -> None:
    service = UsageService()
    collector = UsageCollector(service)

    async def mock_stream() -> AsyncIterator[ProviderChatResult]:
        yield ProviderChatResult(
            model="actual-model",
            created_at=None,
            role="assistant",
            content="hi",
            done=True,
            done_reason="stop",
            prompt_tokens=5,
            completion_tokens=5,
        )

    async for _ in collector.record_stream(
        context=_make_context(), stream=mock_stream(), model="requested-model"
    ):
        pass

    assert service._records[0].model == "actual-model"


@pytest.mark.asyncio
async def test_record_stream_records_on_exception() -> None:
    service = UsageService()
    collector = UsageCollector(service)

    async def failing_stream() -> AsyncIterator[ProviderChatResult]:
        yield ProviderChatResult(
            model="qwen3:4b",
            created_at=None,
            role="assistant",
            content="partial",
            done=False,
            done_reason=None,
            prompt_tokens=3,
            completion_tokens=1,
        )
        raise RuntimeError("provider disconnected")

    with pytest.raises(RuntimeError, match="provider disconnected"):
        async for _ in collector.record_stream(
            context=_make_context(), stream=failing_stream(), model="qwen3:4b"
        ):
            pass

    assert service.record_count == 1
    record = service._records[0]
    assert record.prompt_tokens == 3
    assert record.completion_tokens == 1
    assert record.total_tokens == 4


@pytest.mark.asyncio
async def test_record_stream_no_tokens_defaults_to_zero() -> None:
    service = UsageService()
    collector = UsageCollector(service)

    async def mock_stream() -> AsyncIterator[ProviderChatResult]:
        yield ProviderChatResult(
            model="qwen3:4b",
            created_at=None,
            role="assistant",
            content="hi",
            done=True,
            done_reason="stop",
        )

    async for _ in collector.record_stream(
        context=_make_context(), stream=mock_stream(), model="qwen3:4b"
    ):
        pass

    record = service._records[0]
    assert record.prompt_tokens == 0
    assert record.completion_tokens == 0
