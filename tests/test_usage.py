from app.usage.models import UsageRecord
from app.usage.service import UsageService


def test_record_and_summary() -> None:
    service = UsageService()
    service.record(
        UsageRecord(
            request_id="r1",
            model="llama3",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            latency_ms=100.0,
            api_key_name="test-key",
        )
    )
    summary = service.get_summary()
    assert summary.total_requests == 1
    assert summary.total_prompt_tokens == 10
    assert summary.total_completion_tokens == 20
    assert summary.total_tokens == 30


def test_by_model_aggregation() -> None:
    service = UsageService()
    service.record(
        UsageRecord(
            request_id="r1",
            model="llama3",
            prompt_tokens=5,
            completion_tokens=10,
            total_tokens=15,
            latency_ms=50.0,
            api_key_name=None,
        )
    )
    service.record(
        UsageRecord(
            request_id="r2",
            model="llama3",
            prompt_tokens=3,
            completion_tokens=7,
            total_tokens=10,
            latency_ms=40.0,
            api_key_name=None,
        )
    )
    service.record(
        UsageRecord(
            request_id="r3",
            model="mistral",
            prompt_tokens=2,
            completion_tokens=4,
            total_tokens=6,
            latency_ms=30.0,
            api_key_name=None,
        )
    )
    summary = service.get_summary()
    assert summary.total_requests == 3
    assert summary.by_model["llama3"]["requests"] == 2
    assert summary.by_model["llama3"]["total_tokens"] == 25
    assert summary.by_model["mistral"]["requests"] == 1
    assert summary.by_model["mistral"]["total_tokens"] == 6


def test_max_records_deque() -> None:
    service = UsageService()
    for i in range(1100):
        service.record(
            UsageRecord(
                request_id=f"r{i}",
                model="llama3",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=1.0,
                api_key_name=None,
            )
        )
    assert service.record_count == 1000


def test_api_key_name_stored() -> None:
    service = UsageService()
    service.record(
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
    assert service._records[0].api_key_name == "admin"


def test_empty_summary() -> None:
    service = UsageService()
    summary = service.get_summary()
    assert summary.total_requests == 0
    assert summary.total_tokens == 0
    assert summary.by_model == {}
