import asyncio
import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text

from app.db.init import dispose_db, init_db
from app.db.session import create_async_session_factory
from app.quota.models import QuotaConfig
from app.quota.postgres_repository import PostgresQuotaRepository
from app.quota.service import QuotaService
from app.usage.postgres_repository import PostgresUsageRepository

_SKIP_REASON = "Set INTEGRATION_TEST=1 to run PostgreSQL integration tests"

pytestmark = pytest.mark.skipif(
    not os.getenv("INTEGRATION_TEST"),
    reason=_SKIP_REASON,
)


@pytest.fixture()
async def quota_service() -> AsyncGenerator[QuotaService, None]:
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        database_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        await init_db(database_url)
        factory = create_async_session_factory()
        usage_repo = PostgresUsageRepository(factory)
        quota_repo = PostgresQuotaRepository(factory, usage_repo)
        config = QuotaConfig(
            daily_token_limit=200,
            monthly_token_limit=500,
            default_reserve_tokens=100,
            reservation_ttl_seconds=600,
        )
        service = QuotaService(
            usage_repository=usage_repo,
            quota_repository=quota_repo,
            config=config,
        )
        yield service
        await dispose_db()


@pytest.mark.asyncio
async def test_reserve_and_settle(quota_service: QuotaService) -> None:
    reservation = await quota_service.reserve("hash1", max_tokens=50)
    assert reservation is not None
    assert reservation.reserved_tokens == 50

    await quota_service.settle(reservation.reservation_id)

    quota_repo = quota_service._quota_repo
    assert isinstance(quota_repo, PostgresQuotaRepository)
    async with quota_repo._session_factory() as session:
        remaining = await session.scalar(
            text("SELECT count(*) FROM quota_reservations WHERE id = :id"),
            {"id": reservation.reservation_id},
        )
    assert remaining == 0

    reservation2 = await quota_service.reserve("hash1", max_tokens=50)
    assert reservation2 is not None


@pytest.mark.asyncio
async def test_reserve_and_release(quota_service: QuotaService) -> None:
    reservation = await quota_service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    await quota_service.release(reservation.reservation_id)

    reservation2 = await quota_service.reserve("hash1", max_tokens=100)
    assert reservation2 is not None


@pytest.mark.asyncio
async def test_renew_active_reservation(quota_service: QuotaService) -> None:
    reservation = await quota_service.reserve("hash1", max_tokens=100)
    assert reservation is not None

    assert await quota_service.renew(reservation.reservation_id)
    await quota_service.settle(reservation.reservation_id)
    assert not await quota_service.renew(reservation.reservation_id)


@pytest.mark.asyncio
async def test_daily_limit_enforced(quota_service: QuotaService) -> None:
    first = await quota_service.reserve("hash1", max_tokens=150)
    assert first is not None

    from app.exceptions.base import QuotaExceededError

    with pytest.raises(QuotaExceededError, match="Daily token quota exceeded"):
        await quota_service.reserve("hash1", max_tokens=100)


@pytest.mark.asyncio
async def test_concurrent_reserve_oversubscription_prevented(
    quota_service: QuotaService,
) -> None:
    results: list[object] = []

    async def try_reserve() -> None:
        try:
            r = await quota_service.reserve("hash1", max_tokens=150)
            results.append(r)
        except Exception as exc:
            results.append(exc)

    await asyncio.gather(*[try_reserve() for _ in range(5)])

    successes = [r for r in results if not isinstance(r, Exception)]
    assert len(successes) == 1, (
        f"Expected exactly 1 successful reservation, got {len(successes)}"
    )


@pytest.mark.asyncio
async def test_different_keys_independent(quota_service: QuotaService) -> None:
    first = await quota_service.reserve("hash1", max_tokens=200)
    assert first is not None

    second = await quota_service.reserve("hash2", max_tokens=200)
    assert second is not None


@pytest.mark.asyncio
async def test_cleanup_expired(quota_service: QuotaService) -> None:
    count = await quota_service.cleanup_expired()
    assert count == 0
