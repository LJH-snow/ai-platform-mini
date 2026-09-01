import asyncio
from collections.abc import AsyncIterator

import pytest

from app.adapters.openai_adapter import OpenAIAdapter
from app.core.context import RequestContext
from app.exceptions.base import QuotaReservationError
from app.providers.mock import MockProvider
from app.quota.memory_repository import InMemoryQuotaRepository
from app.quota.models import QuotaConfig
from app.quota.service import QuotaService
from app.schemas.openai import OpenAIChatMessage, OpenAIChatRequest
from app.services.chat_service import ChatService
from app.services.openai_service import OpenAIService
from app.usage.collector import UsageCollector
from app.usage.memory_repository import InMemoryUsageRepository
from app.usage.service import UsageService

_test_context = RequestContext(request_id="test-req")


@pytest.fixture
def openai_service() -> OpenAIService:
    usage_service = UsageService(repository=InMemoryUsageRepository())
    return OpenAIService(
        chat_service=ChatService(provider=MockProvider()),
        usage_collector=UsageCollector(usage_service),
        adapter=OpenAIAdapter(),
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
async def test_stream_yields_complete_sse_sequence(
    openai_service: OpenAIService,
) -> None:
    import json

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

    assert len(chunks) == 7
    assert chunks[-1] == "data: [DONE]\n\n"

    shared_id: str | None = None
    shared_created: int | None = None
    shared_model: str | None = None

    for _, chunk in enumerate(chunks[:-1]):
        assert chunk.startswith("data: ")
        data = json.loads(chunk[6:].strip())
        assert data["object"] == "chat.completion.chunk"

        if shared_id is None:
            shared_id = data["id"]
            shared_created = data["created"]
            shared_model = data["model"]
        else:
            assert data["id"] == shared_id
            assert data["created"] == shared_created
            assert data["model"] == shared_model

    first_data = json.loads(chunks[0][6:].strip())
    assert first_data["choices"][0]["delta"]["role"] == "assistant"
    assert first_data["choices"][0]["finish_reason"] is None

    for i in range(1, 5):
        content_data = json.loads(chunks[i][6:].strip())
        assert "content" in content_data["choices"][0]["delta"]
        assert content_data["choices"][0]["finish_reason"] is None

    final_data = json.loads(chunks[5][6:].strip())
    assert final_data["choices"][0]["finish_reason"] == "stop"


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


@pytest.mark.asyncio
async def test_stream_empty_provider_yields_fallback_chunk() -> None:
    import json

    service = OpenAIService(
        chat_service=ChatService(provider=_EmptyStreamProvider()),
        usage_collector=UsageCollector(
            UsageService(repository=InMemoryUsageRepository())
        ),
        adapter=OpenAIAdapter(),
    )
    request = OpenAIChatRequest(
        messages=[OpenAIChatMessage(role="user", content="Hi")],
        stream=True,
    )
    chunks = [
        chunk
        async for chunk in service.chat_completions_stream(
            request, context=_test_context
        )
    ]

    assert len(chunks) == 2

    data = json.loads(chunks[0][6:].strip())
    assert data["object"] == "chat.completion.chunk"
    assert data["choices"][0]["delta"]["role"] == "assistant"
    assert data["choices"][0]["finish_reason"] == "stop"

    assert chunks[1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_stream_empty_provider_uses_requested_model_for_fallback_chunk() -> None:
    import json

    service = OpenAIService(
        chat_service=ChatService(provider=_EmptyStreamProvider()),
        usage_collector=UsageCollector(
            UsageService(repository=InMemoryUsageRepository())
        ),
        adapter=OpenAIAdapter(),
    )
    request = OpenAIChatRequest(
        model="gpt-4o",
        messages=[OpenAIChatMessage(role="user", content="Hi")],
        stream=True,
    )
    chunks = [
        chunk
        async for chunk in service.chat_completions_stream(
            request, context=_test_context
        )
    ]

    data = json.loads(chunks[0][6:].strip())
    assert data["model"] == "gpt-4o"
    assert data["choices"][0]["finish_reason"] == "stop"


class _UsageAssertingQuotaService(QuotaService):
    def __init__(self, usage_repository: InMemoryUsageRepository) -> None:
        super().__init__(
            usage_repository=usage_repository,
            quota_repository=InMemoryQuotaRepository(usage_repository),
            config=QuotaConfig(
                daily_token_limit=100,
                reservation_ttl_seconds=600,
                reservation_renewal_seconds=60,
            ),
        )
        self.settled_after_usage = False
        self._usage_repository_for_test = usage_repository

    async def settle(self, reservation_id: str) -> None:
        self.settled_after_usage = self._usage_repository_for_test.record_count == 1
        await super().settle(reservation_id)


class _FalseRenewalQuotaService(QuotaService):
    @property
    def reservation_renewal_seconds(self) -> int:
        return 0

    async def renew(self, reservation_id: str) -> bool:
        return False


class _SlowMockProvider(MockProvider):
    async def chat(self, payload: dict[str, object]) -> dict[str, object]:
        await asyncio.sleep(2.1)
        return await super().chat(payload)


class _EmptyStreamProvider(MockProvider):
    async def chat_stream(
        self, payload: dict[str, object]
    ) -> AsyncIterator[dict[str, object]]:
        return
        yield


class _TokenUsageMockProvider(MockProvider):
    async def chat_stream(
        self, payload: dict[str, object]
    ) -> AsyncIterator[dict[str, object]]:
        yield {
            "model": self.default_model,
            "created_at": "2026-08-03T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": "Hello",
            },
            "done": False,
        }
        yield {
            "model": self.default_model,
            "created_at": "2026-08-03T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": "",
            },
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 11,
            "eval_count": 7,
        }


@pytest.mark.asyncio
async def test_stream_records_final_token_usage_through_openai_service() -> None:
    usage_repository = InMemoryUsageRepository()
    service = OpenAIService(
        chat_service=ChatService(provider=_TokenUsageMockProvider()),
        usage_collector=UsageCollector(
            UsageService(repository=usage_repository),
        ),
        adapter=OpenAIAdapter(),
    )
    request = OpenAIChatRequest(
        messages=[OpenAIChatMessage(role="user", content="Hi")],
        stream=True,
    )

    _ = [
        chunk
        async for chunk in service.chat_completions_stream(
            request,
            context=RequestContext(request_id="stream-token-usage", api_key="hash1"),
        )
    ]

    assert usage_repository.record_count == 1
    record = usage_repository._records[0]
    assert record.prompt_tokens == 11
    assert record.completion_tokens == 7
    assert record.total_tokens == 18


@pytest.mark.asyncio
async def test_stream_settles_reservation_after_usage_is_recorded() -> None:
    usage_repository = InMemoryUsageRepository()
    usage_service = UsageService(repository=usage_repository)
    service = OpenAIService(
        chat_service=ChatService(provider=MockProvider()),
        usage_collector=UsageCollector(usage_service),
        adapter=OpenAIAdapter(),
    )
    quota_service = _UsageAssertingQuotaService(usage_repository)
    reservation = await quota_service.reserve("hash1", max_tokens=50)
    assert reservation is not None
    request = OpenAIChatRequest(
        messages=[OpenAIChatMessage(role="user", content="Hi")],
        stream=True,
    )

    _ = [
        chunk
        async for chunk in service.chat_completions_stream(
            request,
            context=RequestContext(request_id="stream-req", api_key="hash1"),
            reservation=reservation,
            quota_service=quota_service,
        )
    ]

    assert usage_repository.record_count == 1
    assert quota_service.settled_after_usage


@pytest.mark.asyncio
async def test_non_stream_renews_reservation_past_initial_ttl() -> None:
    usage_repository = InMemoryUsageRepository()
    quota_service = QuotaService(
        usage_repository=usage_repository,
        quota_repository=InMemoryQuotaRepository(usage_repository),
        config=QuotaConfig(
            daily_token_limit=100,
            reservation_ttl_seconds=2,
            reservation_renewal_seconds=1,
        ),
    )
    reservation = await quota_service.reserve("hash1", max_tokens=50)
    assert reservation is not None
    service = OpenAIService(
        chat_service=ChatService(provider=_SlowMockProvider()),
        usage_collector=UsageCollector(UsageService(repository=usage_repository)),
        adapter=OpenAIAdapter(),
    )
    request = OpenAIChatRequest(
        messages=[OpenAIChatMessage(role="user", content="Hi")],
    )

    response = await service.chat_completions(
        request,
        context=RequestContext(request_id="non-stream-req", api_key="hash1"),
        reservation=reservation,
        quota_service=quota_service,
    )

    assert response.choices[0].message.content == "Hello from Mock Provider"
    assert usage_repository.record_count == 1


@pytest.mark.asyncio
async def test_stream_renewal_failure_stops_stream_and_releases_reservation() -> None:
    usage_repository = InMemoryUsageRepository()
    quota_service = _FalseRenewalQuotaService(
        usage_repository=usage_repository,
        quota_repository=InMemoryQuotaRepository(usage_repository),
        config=QuotaConfig(daily_token_limit=100),
    )
    reservation = await quota_service.reserve("hash1", max_tokens=50)
    assert reservation is not None
    service = OpenAIService(
        chat_service=ChatService(provider=MockProvider()),
        usage_collector=UsageCollector(UsageService(repository=usage_repository)),
        adapter=OpenAIAdapter(),
    )
    request = OpenAIChatRequest(
        messages=[OpenAIChatMessage(role="user", content="Hi")],
        stream=True,
    )

    with pytest.raises(QuotaReservationError, match="could not be renewed"):
        _ = [
            chunk
            async for chunk in service.chat_completions_stream(
                request,
                context=RequestContext(request_id="stream-req", api_key="hash1"),
                reservation=reservation,
                quota_service=quota_service,
            )
        ]

    assert usage_repository.record_count == 1
    assert (
        await quota_service._quota_repo.get_reserved_tokens_for_key(
            "hash1", reservation.usage_date
        )
        == 0
    )


@pytest.mark.asyncio
async def test_stream_disconnect_records_usage_and_releases_reservation() -> None:
    usage_repository = InMemoryUsageRepository()
    quota_repository = InMemoryQuotaRepository(usage_repository)
    quota_service = QuotaService(
        usage_repository=usage_repository,
        quota_repository=quota_repository,
        config=QuotaConfig(daily_token_limit=100),
    )
    reservation = await quota_service.reserve("hash1", max_tokens=50)
    assert reservation is not None
    service = OpenAIService(
        chat_service=ChatService(provider=MockProvider()),
        usage_collector=UsageCollector(UsageService(repository=usage_repository)),
        adapter=OpenAIAdapter(),
    )
    request = OpenAIChatRequest(
        messages=[OpenAIChatMessage(role="user", content="Hi")],
        stream=True,
    )
    stream = service.chat_completions_stream(
        request,
        context=RequestContext(request_id="disconnect-req", api_key="hash1"),
        reservation=reservation,
        quota_service=quota_service,
    )

    await anext(stream)
    await stream.aclose()

    assert usage_repository.record_count == 1
    assert (
        await quota_repository.get_reserved_tokens_for_key(
            "hash1", reservation.usage_date
        )
        == 0
    )
