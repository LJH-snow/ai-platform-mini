import asyncio

import pytest

from app.exceptions.base import QuotaReservationError
from app.quota.lifecycle import ReservationLifecycle
from app.quota.memory_repository import InMemoryQuotaRepository
from app.quota.models import QuotaConfig
from app.quota.service import QuotaService
from app.usage.memory_repository import InMemoryUsageRepository


class _FalseRenewalQuotaService(QuotaService):
    @property
    def reservation_renewal_seconds(self) -> int:
        return 0

    async def renew(self, reservation_id: str) -> bool:
        return False


class _FailingRenewalQuotaService(QuotaService):
    @property
    def reservation_renewal_seconds(self) -> int:
        return 0

    async def renew(self, reservation_id: str) -> bool:
        raise RuntimeError("database unavailable")


def _make_quota_service(
    service_class: type[QuotaService] = QuotaService,
    ttl_seconds: int = 600,
    renewal_seconds: int = 60,
) -> tuple[QuotaService, InMemoryQuotaRepository]:
    usage_repository = InMemoryUsageRepository()
    quota_repository = InMemoryQuotaRepository(usage_repository)
    service = service_class(
        usage_repository=usage_repository,
        quota_repository=quota_repository,
        config=QuotaConfig(
            daily_token_limit=500,
            reservation_ttl_seconds=ttl_seconds,
            reservation_renewal_seconds=renewal_seconds,
        ),
    )
    return service, quota_repository


@pytest.mark.asyncio
async def test_non_stream_operation_renews_past_initial_ttl() -> None:
    quota_service, quota_repository = _make_quota_service(
        ttl_seconds=2, renewal_seconds=1
    )
    reservation = await quota_service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    async with ReservationLifecycle(reservation, quota_service) as lifecycle:
        result = await lifecycle.run(asyncio.sleep(2.1, result="completed"))
        assert result == "completed"
        assert (
            await quota_repository.get_reserved_tokens_for_key(
                "hash1", reservation.usage_date
            )
            == 100
        )
        await lifecycle.settle()


@pytest.mark.asyncio
async def test_false_renewal_stops_operation_and_releases_reservation() -> None:
    quota_service, quota_repository = _make_quota_service(_FalseRenewalQuotaService)
    reservation = await quota_service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    with pytest.raises(QuotaReservationError, match="could not be renewed"):
        async with ReservationLifecycle(reservation, quota_service) as lifecycle:
            await lifecycle.run(asyncio.sleep(1))

    assert (
        await quota_repository.get_reserved_tokens_for_key(
            "hash1", reservation.usage_date
        )
        == 0
    )


@pytest.mark.asyncio
async def test_renewal_exception_stops_operation_and_releases_reservation() -> None:
    quota_service, quota_repository = _make_quota_service(_FailingRenewalQuotaService)
    reservation = await quota_service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    with pytest.raises(QuotaReservationError, match="renewal failed"):
        async with ReservationLifecycle(reservation, quota_service) as lifecycle:
            await lifecycle.run(asyncio.sleep(1))

    assert (
        await quota_repository.get_reserved_tokens_for_key(
            "hash1", reservation.usage_date
        )
        == 0
    )


@pytest.mark.asyncio
async def test_cancelling_run_cancels_operation_and_releases_reservation() -> None:
    quota_service, quota_repository = _make_quota_service()
    reservation = await quota_service.reserve("hash1", max_tokens=100)
    assert reservation is not None
    operation_started = asyncio.Event()
    operation_cancelled = asyncio.Event()

    async def operation() -> None:
        operation_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            operation_cancelled.set()
            raise

    async def run_operation() -> None:
        async with ReservationLifecycle(reservation, quota_service) as lifecycle:
            await lifecycle.run(operation())

    request_task = asyncio.create_task(run_operation())
    await operation_started.wait()
    request_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await request_task

    assert operation_cancelled.is_set()
    assert (
        await quota_repository.get_reserved_tokens_for_key(
            "hash1", reservation.usage_date
        )
        == 0
    )
