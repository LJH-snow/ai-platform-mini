import asyncio
from datetime import UTC, datetime

import pytest

from app.exceptions.base import QuotaExceededError
from app.quota.memory_repository import InMemoryQuotaRepository
from app.quota.models import QuotaConfig
from app.quota.service import QuotaService
from app.usage.memory_repository import InMemoryUsageRepository
from app.usage.models import UsageRecord


def _make_service(
    daily: int | None = None,
    monthly: int | None = None,
    ttl: int = 600,
) -> tuple[QuotaService, InMemoryUsageRepository, InMemoryQuotaRepository]:
    usage_repo = InMemoryUsageRepository()
    quota_repo = InMemoryQuotaRepository(usage_repo)
    config = QuotaConfig(
        daily_token_limit=daily,
        monthly_token_limit=monthly,
        default_reserve_tokens=100,
        reservation_ttl_seconds=ttl,
    )
    return (
        QuotaService(
            usage_repository=usage_repo,
            quota_repository=quota_repo,
            config=config,
        ),
        usage_repo,
        quota_repo,
    )


@pytest.mark.asyncio
async def test_no_quota_configured_reserve_returns_none() -> None:
    service, _, _ = _make_service()
    result = await service.reserve("hash1")
    assert result is None


@pytest.mark.asyncio
async def test_reserve_within_daily_limit() -> None:
    service, _, quota_repo = _make_service(daily=200)
    reservation = await service.reserve("hash1", max_tokens=50)
    assert reservation is not None
    assert reservation.reserved_tokens == 50
    reserved = await quota_repo.get_reserved_tokens_for_key(
        "hash1", reservation.usage_date
    )
    assert reserved == 50


@pytest.mark.asyncio
async def test_reserve_exceeds_daily_limit() -> None:
    service, _, _ = _make_service(daily=100)
    first = await service.reserve("hash1", max_tokens=80)
    assert first is not None

    with pytest.raises(
        QuotaExceededError, match="Daily token quota exceeded"
    ) as exc_info:
        await service.reserve("hash1", max_tokens=50)
    assert exc_info.value.retry_after > 0


@pytest.mark.asyncio
async def test_reserve_exceeds_monthly_limit() -> None:
    service, usage_repo, _ = _make_service(monthly=200)
    await usage_repo.record_usage(
        UsageRecord(
            request_id="r1",
            model="llama3",
            total_tokens=180,
            api_key_hash="hash1",
            usage_date="2026-08-01",
        )
    )
    with pytest.raises(
        QuotaExceededError, match="Monthly token quota exceeded"
    ) as exc_info:
        await service.reserve("hash1", max_tokens=50)
    assert exc_info.value.retry_after > 0


@pytest.mark.asyncio
async def test_settle_marks_reservation_settled() -> None:
    service, _, quota_repo = _make_service(daily=500)
    reservation = await service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    await service.settle(reservation.reservation_id)

    reserved = await quota_repo.get_reserved_tokens_for_key(
        "hash1", reservation.usage_date
    )
    assert reserved == 0
    assert reservation.reservation_id not in quota_repo._reservations


@pytest.mark.asyncio
async def test_release_removes_reservation() -> None:
    service, _, quota_repo = _make_service(daily=500)
    reservation = await service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    await service.release(reservation.reservation_id)

    reserved = await quota_repo.get_reserved_tokens_for_key(
        "hash1", reservation.usage_date
    )
    assert reserved == 0


@pytest.mark.asyncio
async def test_renew_keeps_active_reservation_reserved() -> None:
    service, _, quota_repo = _make_service(daily=500)
    reservation = await service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    assert await service.renew(reservation.reservation_id)
    reserved = await quota_repo.get_reserved_tokens_for_key(
        "hash1", reservation.usage_date
    )
    assert reserved == 100

    await service.settle(reservation.reservation_id)
    assert not await service.renew(reservation.reservation_id)


@pytest.mark.asyncio
async def test_different_keys_independent() -> None:
    service, _, _ = _make_service(daily=100)
    first = await service.reserve("hash1", max_tokens=100)
    assert first is not None

    with pytest.raises(QuotaExceededError):
        await service.reserve("hash1", max_tokens=10)

    second = await service.reserve("hash2")
    assert second is not None


@pytest.mark.asyncio
async def test_reserve_uses_default_when_no_max_tokens() -> None:
    service, _, _ = _make_service(daily=500)
    reservation = await service.reserve("hash1")
    assert reservation is not None
    assert reservation.reserved_tokens == 100


@pytest.mark.asyncio
async def test_daily_retry_after_until_next_utc_day() -> None:
    service, _, _ = _make_service(daily=0)
    with pytest.raises(QuotaExceededError) as exc_info:
        await service.reserve("hash1")
    assert 0 < exc_info.value.retry_after <= 86400


@pytest.mark.asyncio
async def test_monthly_retry_after_until_next_month() -> None:
    service, _, _ = _make_service(monthly=0)
    with pytest.raises(QuotaExceededError) as exc_info:
        await service.reserve("hash1")
    assert exc_info.value.retry_after > 86400


@pytest.mark.asyncio
async def test_quota_config_disabled() -> None:
    config = QuotaConfig(daily_token_limit=None, monthly_token_limit=None)
    assert not config.enabled


@pytest.mark.asyncio
async def test_reservation_does_not_pollute_usage() -> None:
    service, usage_repo, _ = _make_service(daily=500)
    reservation = await service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    await usage_repo.record_usage(
        UsageRecord(
            request_id="r1",
            model="llama3",
            total_tokens=60,
            api_key_hash="hash1",
            usage_date=reservation.usage_date,
        )
    )

    tokens = await usage_repo.get_total_tokens_for_key("hash1", reservation.usage_date)
    assert tokens == 60

    await service.settle(reservation.reservation_id)

    tokens_after = await usage_repo.get_total_tokens_for_key(
        "hash1", reservation.usage_date
    )
    assert tokens_after == 60


@pytest.mark.asyncio
async def test_expired_reservation_not_counted() -> None:
    service, _, quota_repo = _make_service(daily=500, ttl=0)
    reservation = await service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    await asyncio.sleep(0.05)

    reserved = await quota_repo.get_reserved_tokens_for_key(
        "hash1", reservation.usage_date
    )
    assert reserved == 0


@pytest.mark.asyncio
async def test_cleanup_expired_removes_stale() -> None:
    service, _, quota_repo = _make_service(daily=500, ttl=0)
    reservation = await service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    await asyncio.sleep(0.05)

    count = await service.cleanup_expired()
    assert count == 1

    remaining = await quota_repo.get_reserved_tokens_for_key(
        "hash1", reservation.usage_date
    )
    assert remaining == 0


@pytest.mark.asyncio
async def test_cleanup_expired_keeps_active() -> None:
    service, _, quota_repo = _make_service(daily=500, ttl=600)
    reservation = await service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    count = await service.cleanup_expired()
    assert count == 0

    reserved = await quota_repo.get_reserved_tokens_for_key(
        "hash1", reservation.usage_date
    )
    assert reserved == 100


@pytest.mark.asyncio
async def test_expired_reservation_frees_quota() -> None:
    service, _, _ = _make_service(daily=100, ttl=0)
    first = await service.reserve("hash1", max_tokens=100)
    assert first is not None

    await asyncio.sleep(0.05)

    await service.cleanup_expired()

    second = await service.reserve("hash1", max_tokens=50)
    assert second is not None


@pytest.mark.asyncio
async def test_settled_reservation_is_removed_before_cleanup() -> None:
    service, _, quota_repo = _make_service(daily=500, ttl=0)
    reservation = await service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    await service.settle(reservation.reservation_id)
    await asyncio.sleep(0.05)

    count = await service.cleanup_expired()
    assert count == 0
    assert reservation.reservation_id not in quota_repo._reservations


@pytest.mark.asyncio
async def test_cleanup_removes_legacy_settled_reservation() -> None:
    service, _, quota_repo = _make_service(daily=500)
    reservation = await service.reserve("hash1", max_tokens=100)
    assert reservation is not None
    quota_repo._reservations[reservation.reservation_id]["settled"] = True

    count = await service.cleanup_expired()

    assert count == 1
    assert reservation.reservation_id not in quota_repo._reservations


@pytest.mark.asyncio
async def test_reserve_includes_prompt_tokens() -> None:
    service, _, _ = _make_service(daily=200)

    reservation = await service.reserve(
        "hash1",
        prompt_tokens=80,
        max_tokens=50,
    )

    assert reservation is not None
    assert reservation.reserved_tokens == 130


@pytest.mark.asyncio
async def test_monthly_limit_is_reported_when_daily_limit_is_also_configured() -> None:
    service, usage_repo, _ = _make_service(daily=1_000, monthly=200)
    usage_date = datetime.now(UTC).strftime("%Y-%m-%d")
    await usage_repo.record_usage(
        UsageRecord(
            request_id="r1",
            model="llama3",
            total_tokens=180,
            api_key_hash="hash1",
            usage_date=usage_date,
        )
    )

    with pytest.raises(
        QuotaExceededError, match="Monthly token quota exceeded"
    ) as exc_info:
        await service.reserve("hash1", max_tokens=50)

    assert exc_info.value.retry_after > 86400


@pytest.mark.asyncio
async def test_daily_limit_counts_usage_plus_reserved() -> None:
    service, usage_repo, _ = _make_service(daily=200)
    reservation = await service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    await usage_repo.record_usage(
        UsageRecord(
            request_id="r1",
            model="llama3",
            total_tokens=120,
            api_key_hash="hash1",
            usage_date=reservation.usage_date,
        )
    )

    with pytest.raises(
        QuotaExceededError, match="Daily token quota exceeded"
    ) as exc_info:
        await service.reserve("hash1", max_tokens=50)
    assert exc_info.value.retry_after > 0


@pytest.mark.asyncio
async def test_extend_reservation_increases_reserved_tokens() -> None:
    service, _, quota_repo = _make_service(daily=200)
    reservation = await service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    await service.extend(reservation.reservation_id, 50)

    reserved = await quota_repo.get_reserved_tokens_for_key(
        "hash1", reservation.usage_date
    )
    assert reserved == 150


@pytest.mark.asyncio
async def test_extend_reservation_rejects_daily_limit_without_mutation() -> None:
    service, _, quota_repo = _make_service(daily=100)
    reservation = await service.reserve("hash1", max_tokens=80)
    assert reservation is not None

    with pytest.raises(QuotaExceededError, match="Daily token quota exceeded"):
        await service.extend(reservation.reservation_id, 21)

    reserved = await quota_repo.get_reserved_tokens_for_key(
        "hash1", reservation.usage_date
    )
    assert reserved == 80


@pytest.mark.asyncio
async def test_extend_reservation_rejects_monthly_limit_without_mutation() -> None:
    service, _, quota_repo = _make_service(monthly=100)
    reservation = await service.reserve("hash1", max_tokens=80)
    assert reservation is not None

    with pytest.raises(QuotaExceededError, match="Monthly token quota exceeded"):
        await service.extend(reservation.reservation_id, 21)

    reserved = await quota_repo.get_monthly_reserved_tokens_for_key(
        "hash1", reservation.usage_date[:7]
    )
    assert reserved == 80
