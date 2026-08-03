from collections.abc import Generator
from datetime import UTC, datetime

import pytest

from app.core.container import (
    provide_quota_service,
    provide_usage_repository,
    provide_usage_service,
)
from app.core.settings import get_settings
from app.exceptions.base import QuotaExceededError
from app.usage.models import UsageRecord


@pytest.fixture()
def memory_container(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("AUTH_STORAGE", "memory")
    monkeypatch.setenv("QUOTA_DAILY_TOKENS", "100")
    get_settings.cache_clear()
    provide_usage_repository.cache_clear()
    provide_usage_service.cache_clear()
    provide_quota_service.cache_clear()
    yield
    provide_quota_service.cache_clear()
    provide_usage_service.cache_clear()
    provide_usage_repository.cache_clear()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_memory_usage_and_quota_share_repository(
    memory_container: None,
) -> None:
    usage_service = provide_usage_service()
    quota_service = provide_quota_service()
    usage_date = datetime.now(UTC).strftime("%Y-%m-%d")

    await usage_service.record(
        UsageRecord(
            request_id="request-1",
            model="mock-model",
            total_tokens=60,
            api_key_hash="hash1",
            usage_date=usage_date,
        )
    )

    with pytest.raises(QuotaExceededError, match="Daily token quota exceeded"):
        await quota_service.reserve("hash1", max_tokens=50)
