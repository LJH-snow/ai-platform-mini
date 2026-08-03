import os
from collections.abc import AsyncGenerator

import pytest

from app.db.init import dispose_db, init_db
from app.db.session import create_async_session_factory
from app.usage.models import UsageRecord
from app.usage.postgres_repository import PostgresUsageRepository

_SKIP_REASON = "Set INTEGRATION_TEST=1 to run PostgreSQL integration tests"

pytestmark = pytest.mark.skipif(
    not os.getenv("INTEGRATION_TEST"),
    reason=_SKIP_REASON,
)


@pytest.fixture()
async def usage_repo() -> AsyncGenerator[PostgresUsageRepository, None]:
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        database_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        await init_db(database_url)
        factory = create_async_session_factory()
        repo = PostgresUsageRepository(factory)
        yield repo
        await dispose_db()


@pytest.mark.asyncio
async def test_record_and_get_daily(usage_repo: PostgresUsageRepository) -> None:
    await usage_repo.record_usage(
        UsageRecord(
            request_id="r1",
            model="llama3",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            api_key_hash="hash1",
            usage_date="2026-08-03",
        )
    )
    daily = await usage_repo.get_daily_usage("hash1", "2026-08-03")
    assert len(daily) == 1
    assert daily[0].model == "llama3"
    assert daily[0].request_count == 1
    assert daily[0].total_tokens == 30


@pytest.mark.asyncio
async def test_upsert_accumulates(usage_repo: PostgresUsageRepository) -> None:
    for i in range(3):
        await usage_repo.record_usage(
            UsageRecord(
                request_id=f"r{i}",
                model="llama3",
                prompt_tokens=5,
                completion_tokens=5,
                total_tokens=10,
                api_key_hash="hash1",
                usage_date="2026-08-03",
            )
        )
    daily = await usage_repo.get_daily_usage("hash1", "2026-08-03")
    assert len(daily) == 1
    assert daily[0].request_count == 3
    assert daily[0].total_tokens == 30


@pytest.mark.asyncio
async def test_monthly_aggregation(usage_repo: PostgresUsageRepository) -> None:
    for day in ["2026-08-01", "2026-08-02"]:
        await usage_repo.record_usage(
            UsageRecord(
                request_id="r1",
                model="llama3",
                total_tokens=50,
                api_key_hash="hash1",
                usage_date=day,
            )
        )
    monthly = await usage_repo.get_monthly_usage("hash1", "2026-08")
    assert len(monthly) == 1
    assert monthly[0].total_tokens == 100
    assert monthly[0].request_count == 2


@pytest.mark.asyncio
async def test_get_daily_tokens(usage_repo: PostgresUsageRepository) -> None:
    await usage_repo.record_usage(
        UsageRecord(
            request_id="r1",
            model="llama3",
            total_tokens=40,
            api_key_hash="hash1",
            usage_date="2026-08-03",
        )
    )
    await usage_repo.record_usage(
        UsageRecord(
            request_id="r2",
            model="mistral",
            total_tokens=30,
            api_key_hash="hash1",
            usage_date="2026-08-03",
        )
    )
    total = await usage_repo.get_total_tokens_for_key("hash1", "2026-08-03")
    assert total == 70


@pytest.mark.asyncio
async def test_different_keys_isolated(
    usage_repo: PostgresUsageRepository,
) -> None:
    await usage_repo.record_usage(
        UsageRecord(
            request_id="r1",
            model="llama3",
            total_tokens=100,
            api_key_hash="hash1",
            usage_date="2026-08-03",
        )
    )
    total2 = await usage_repo.get_total_tokens_for_key("hash2", "2026-08-03")
    assert total2 == 0


@pytest.mark.asyncio
async def test_record_without_hash_skipped(
    usage_repo: PostgresUsageRepository,
) -> None:
    await usage_repo.record_usage(
        UsageRecord(
            request_id="r1",
            model="llama3",
            total_tokens=50,
            api_key_hash="",
            usage_date="2026-08-03",
        )
    )
    total = await usage_repo.get_total_tokens_for_key("", "2026-08-03")
    assert total == 0


@pytest.mark.asyncio
async def test_get_all_summary(usage_repo: PostgresUsageRepository) -> None:
    await usage_repo.record_usage(
        UsageRecord(
            request_id="r1",
            model="llama3",
            total_tokens=30,
            api_key_hash="hash1",
            usage_date="2026-08-03",
        )
    )
    await usage_repo.record_usage(
        UsageRecord(
            request_id="r2",
            model="llama3",
            total_tokens=20,
            api_key_hash="hash1",
            usage_date="2026-08-03",
        )
    )
    summary = await usage_repo.get_all_summary()
    assert summary.total_requests == 2
    assert summary.total_tokens == 50
    assert summary.by_model["llama3"]["requests"] == 2


@pytest.mark.asyncio
async def test_get_summary_for_key_is_isolated(
    usage_repo: PostgresUsageRepository,
) -> None:
    await usage_repo.record_usage(
        UsageRecord(
            request_id="r1",
            model="llama3",
            total_tokens=30,
            api_key_hash="hash1",
            usage_date="2026-08-03",
        )
    )
    await usage_repo.record_usage(
        UsageRecord(
            request_id="r2",
            model="mistral",
            total_tokens=300,
            api_key_hash="hash2",
            usage_date="2026-08-03",
        )
    )

    summary = await usage_repo.get_summary_for_key("hash1")

    assert summary.total_requests == 1
    assert summary.total_tokens == 30
    assert set(summary.by_model) == {"llama3"}
