import pytest

from app.usage.memory_repository import InMemoryUsageRepository
from app.usage.models import UsageRecord
from app.usage.service import UsageService


def _make_service() -> UsageService:
    return UsageService(repository=InMemoryUsageRepository())


@pytest.mark.asyncio
async def test_record_and_summary() -> None:
    service = _make_service()
    await service.record(
        UsageRecord(
            request_id="r1",
            model="llama3",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            latency_ms=100.0,
            api_key_name="test-key",
            api_key_hash="hash1",
        )
    )
    summary = await service.get_summary("hash1")
    assert summary.total_requests == 1
    assert summary.total_prompt_tokens == 10
    assert summary.total_completion_tokens == 20
    assert summary.total_tokens == 30


@pytest.mark.asyncio
async def test_by_model_aggregation() -> None:
    service = _make_service()
    await service.record(
        UsageRecord(
            request_id="r1",
            model="llama3",
            prompt_tokens=5,
            completion_tokens=10,
            total_tokens=15,
            latency_ms=50.0,
            api_key_hash="hash1",
        )
    )
    await service.record(
        UsageRecord(
            request_id="r2",
            model="llama3",
            prompt_tokens=3,
            completion_tokens=7,
            total_tokens=10,
            latency_ms=40.0,
            api_key_hash="hash1",
        )
    )
    await service.record(
        UsageRecord(
            request_id="r3",
            model="mistral",
            prompt_tokens=2,
            completion_tokens=4,
            total_tokens=6,
            latency_ms=30.0,
            api_key_hash="hash1",
        )
    )
    summary = await service.get_summary("hash1")
    assert summary.total_requests == 3
    assert summary.by_model["llama3"]["requests"] == 2
    assert summary.by_model["llama3"]["total_tokens"] == 25
    assert summary.by_model["mistral"]["requests"] == 1
    assert summary.by_model["mistral"]["total_tokens"] == 6


@pytest.mark.asyncio
async def test_max_records_deque() -> None:
    service = _make_service()
    for i in range(1100):
        await service.record(
            UsageRecord(
                request_id=f"r{i}",
                model="llama3",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=1.0,
                api_key_hash="hash1",
            )
        )
    summary = await service.get_summary("hash1")
    assert summary.total_requests == 1000


@pytest.mark.asyncio
async def test_api_key_name_stored() -> None:
    repo = InMemoryUsageRepository()
    service = UsageService(repository=repo)
    await service.record(
        UsageRecord(
            request_id="r1",
            model="llama3",
            prompt_tokens=5,
            completion_tokens=5,
            total_tokens=10,
            latency_ms=50.0,
            api_key_name="admin",
        )
    )
    assert repo.record_count == 1
    records = repo._records
    assert records[0].api_key_name == "admin"


@pytest.mark.asyncio
async def test_empty_summary() -> None:
    service = _make_service()
    summary = await service.get_summary("hash1")
    assert summary.total_requests == 0
    assert summary.total_tokens == 0
    assert summary.by_model == {}


@pytest.mark.asyncio
async def test_summary_is_isolated_by_api_key() -> None:
    service = _make_service()
    await service.record(
        UsageRecord(
            request_id="r1",
            model="llama3",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            api_key_hash="hash1",
        )
    )
    await service.record(
        UsageRecord(
            request_id="r2",
            model="mistral",
            prompt_tokens=100,
            completion_tokens=200,
            total_tokens=300,
            api_key_hash="hash2",
        )
    )

    summary = await service.get_summary("hash1")

    assert summary.total_requests == 1
    assert summary.total_tokens == 30
    assert set(summary.by_model) == {"llama3"}
