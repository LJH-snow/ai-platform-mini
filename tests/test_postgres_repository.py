import os
from collections.abc import AsyncGenerator

import pytest

from app.auth.hash import hash_api_key
from app.auth.models import APIKeyRecord
from app.auth.postgres_repository import PostgresAPIKeyRepository
from app.db.init import dispose_db, init_db
from app.db.session import create_async_session_factory

_SKIP_REASON = "Set INTEGRATION_TEST=1 to run PostgreSQL integration tests"

pytestmark = pytest.mark.skipif(
    not os.getenv("INTEGRATION_TEST"),
    reason=_SKIP_REASON,
)


@pytest.fixture()
async def pg_repo() -> AsyncGenerator[PostgresAPIKeyRepository, None]:
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        database_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        await init_db(database_url)
        factory = create_async_session_factory()
        repo = PostgresAPIKeyRepository(factory)
        yield repo
        await dispose_db()


@pytest.mark.asyncio
async def test_create_and_find_key(pg_repo: PostgresAPIKeyRepository) -> None:
    record = APIKeyRecord(
        key_hash=hash_api_key("sk-integration-test"),
        name="integration",
        status="active",
    )
    saved = await pg_repo.create_key(record)
    assert saved.created_at is not None

    found = await pg_repo.find_by_key_hash(record.key_hash)
    assert found is not None
    assert found.name == "integration"
    assert found.status == "active"
    assert found.created_at is not None


@pytest.mark.asyncio
async def test_ensure_key_idempotent(pg_repo: PostgresAPIKeyRepository) -> None:
    record = APIKeyRecord(
        key_hash=hash_api_key("sk-bootstrap"),
        name="bootstrap",
        status="active",
    )
    created1 = await pg_repo.ensure_key(record)
    assert created1 is True
    created2 = await pg_repo.ensure_key(record)
    assert created2 is False


@pytest.mark.asyncio
async def test_update_status_revoked(pg_repo: PostgresAPIKeyRepository) -> None:
    record = APIKeyRecord(
        key_hash=hash_api_key("sk-revoke-test"),
        name="revoke-me",
        status="active",
    )
    await pg_repo.create_key(record)
    result = await pg_repo.update_status(record.key_hash, "revoked")
    assert result is True

    found = await pg_repo.find_by_key_hash(record.key_hash)
    assert found is not None
    assert found.status == "revoked"


@pytest.mark.asyncio
async def test_find_by_prefix(pg_repo: PostgresAPIKeyRepository) -> None:
    record = APIKeyRecord(
        key_hash=hash_api_key("sk-prefix-test"),
        name="prefix",
        status="active",
    )
    await pg_repo.create_key(record)
    prefix = record.key_hash[:8]
    matches = await pg_repo.find_by_key_hash_prefix(prefix)
    assert len(matches) == 1
    assert matches[0].key_hash == record.key_hash


@pytest.mark.asyncio
async def test_find_by_prefix_no_match(pg_repo: PostgresAPIKeyRepository) -> None:
    matches = await pg_repo.find_by_key_hash_prefix("00000000")
    assert len(matches) == 0


@pytest.mark.asyncio
async def test_touch_last_used(pg_repo: PostgresAPIKeyRepository) -> None:
    record = APIKeyRecord(
        key_hash=hash_api_key("sk-touch-test"),
        name="touch",
        status="active",
    )
    await pg_repo.create_key(record)
    assert record.last_used_at is None

    await pg_repo.touch_last_used(record.key_hash)
    found = await pg_repo.find_by_key_hash(record.key_hash)
    assert found is not None
    assert found.last_used_at is not None
