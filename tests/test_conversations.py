import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest

from app.auth.hash import hash_api_key
from app.conversations.memory_repository import InMemoryConversationRepository
from app.conversations.models import ConversationThread
from app.conversations.postgres_repository import PostgresConversationRepository
from app.conversations.service import ConversationService
from app.db.init import dispose_db, init_db
from app.db.session import create_async_session_factory
from app.exceptions.base import ConversationNotFoundError, ValidationError

OWNER_1 = hash_api_key("sk-owner-1")
OWNER_2 = hash_api_key("sk-owner-2")


def _service() -> ConversationService:
    return ConversationService(repository=InMemoryConversationRepository())


@pytest.mark.asyncio
async def test_memory_create_and_get_thread() -> None:
    service = _service()

    thread = await service.create_thread(OWNER_1, "First thread")

    assert thread.id
    assert thread.owner_key_hash == OWNER_1
    assert thread.title == "First thread"
    assert thread.created_at is not None
    assert thread.updated_at is not None

    fetched = await service.get_thread(OWNER_1, thread.id)
    assert fetched == thread


@pytest.mark.asyncio
async def test_memory_append_messages_and_load_in_order() -> None:
    service = _service()
    thread = await service.create_thread(OWNER_1, "History thread")

    first = await service.append_message(
        OWNER_1, thread.id, "user", "hello", token_count=3
    )
    second = await service.append_message(
        OWNER_1, thread.id, "assistant", "hi", token_count=5
    )
    third = await service.append_message(
        OWNER_1, thread.id, "user", "again", token_count=2
    )

    history = await service.load_history(OWNER_1, thread.id)
    assert [message.role for message in history] == ["user", "assistant", "user"]
    assert [message.content for message in history] == ["hello", "hi", "again"]
    assert [message.token_count for message in history] == [3, 5, 2]
    assert [message.id for message in history] == [first.id, second.id, third.id]
    assert [message.created_at for message in history] == [
        first.created_at,
        second.created_at,
        third.created_at,
    ]

    fetched = await service.get_thread(OWNER_1, thread.id)
    assert fetched.updated_at == history[-1].created_at


@pytest.mark.asyncio
async def test_memory_empty_thread_returns_empty_history() -> None:
    service = _service()
    thread = await service.create_thread(OWNER_1, "Empty thread")

    history = await service.load_history(OWNER_1, thread.id)

    assert history == []


@pytest.mark.asyncio
async def test_memory_repository_isolates_tenants() -> None:
    repo = InMemoryConversationRepository()
    thread = ConversationThread(
        id=str(uuid.uuid4()),
        owner_key_hash=OWNER_1,
        title="Private",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    await repo.create_thread(thread)

    assert await repo.get_thread(thread.id, OWNER_2) is None
    assert await repo.add_message(thread.id, OWNER_2, "user", "sneak") is None
    assert await repo.list_messages(thread.id, OWNER_2) == []


@pytest.mark.asyncio
async def test_service_rejects_foreign_or_missing_threads() -> None:
    service = _service()
    thread = await service.create_thread(OWNER_1, "Private")

    with pytest.raises(ConversationNotFoundError):
        await service.get_thread(OWNER_2, thread.id)
    with pytest.raises(ConversationNotFoundError):
        await service.append_message(OWNER_2, thread.id, "user", "sneak")
    with pytest.raises(ConversationNotFoundError):
        await service.load_history(OWNER_2, thread.id)
    with pytest.raises(ConversationNotFoundError):
        await service.load_history(OWNER_1, "missing-thread")


@pytest.mark.asyncio
async def test_service_validates_message_and_owner_inputs() -> None:
    service = _service()
    thread = await service.create_thread(OWNER_1, "Validation")

    with pytest.raises(ValidationError, match="role"):
        await service.append_message(OWNER_1, thread.id, "admin", "x")
    with pytest.raises(ValidationError, match="empty"):
        await service.append_message(OWNER_1, thread.id, "user", "")
    with pytest.raises(ValidationError, match="negative"):
        await service.append_message(OWNER_1, thread.id, "user", "x", token_count=-1)
    with pytest.raises(ValueError, match="SHA-256"):
        await service.create_thread("not-a-hash", "Bad owner")


@pytest.mark.asyncio
async def test_service_validates_thread_title() -> None:
    service = _service()

    with pytest.raises(ValidationError, match="empty"):
        await service.create_thread(OWNER_1, "")
    with pytest.raises(ValidationError, match="empty"):
        await service.create_thread(OWNER_1, "   ")
    with pytest.raises(ValidationError, match="255"):
        await service.create_thread(OWNER_1, "t" * 256)

    thread = await service.create_thread(OWNER_1, "  title  ")
    assert thread.title == "title"
    long_ok = await service.create_thread(OWNER_1, "t" * 255)
    assert long_ok.title == "t" * 255


class TestPostgresConversationRepository:
    pytestmark = pytest.mark.skipif(
        not os.getenv("INTEGRATION_TEST"),
        reason="Set INTEGRATION_TEST=1 to run PostgreSQL integration tests",
    )

    @pytest.fixture()
    async def pg_repo(
        self,
    ) -> AsyncGenerator[PostgresConversationRepository, None]:
        from testcontainers.community.postgres import PostgresContainer

        with PostgresContainer("postgres:16-alpine") as pg:
            database_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
            await init_db(database_url)
            factory = create_async_session_factory()
            repo = PostgresConversationRepository(factory)
            yield repo
            await dispose_db()

    @pytest.mark.asyncio
    async def test_create_and_get_thread(
        self,
        pg_repo: PostgresConversationRepository,
    ) -> None:
        now = datetime.now(UTC)
        thread = ConversationThread(
            id=str(uuid.uuid4()),
            owner_key_hash=OWNER_1,
            title="PG thread",
            created_at=now,
            updated_at=now,
        )

        saved = await pg_repo.create_thread(thread)

        assert saved == thread
        found = await pg_repo.get_thread(thread.id, OWNER_1)
        assert found == thread

    @pytest.mark.asyncio
    async def test_append_and_load_history_in_order(
        self,
        pg_repo: PostgresConversationRepository,
    ) -> None:
        thread = ConversationThread(
            id=str(uuid.uuid4()),
            owner_key_hash=OWNER_1,
            title="PG history",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await pg_repo.create_thread(thread)

        first = await pg_repo.add_message(
            thread.id, OWNER_1, "user", "hello", token_count=3
        )
        second = await pg_repo.add_message(
            thread.id, OWNER_1, "assistant", "hi", token_count=5
        )
        assert first is not None
        assert second is not None
        assert first.id < second.id

        history = await pg_repo.list_messages(thread.id, OWNER_1)

        assert [message.content for message in history] == ["hello", "hi"]
        assert [message.id for message in history] == [first.id, second.id]
        assert [message.token_count for message in history] == [3, 5]

    @pytest.mark.asyncio
    async def test_empty_thread_returns_empty_history(
        self,
        pg_repo: PostgresConversationRepository,
    ) -> None:
        thread = ConversationThread(
            id=str(uuid.uuid4()),
            owner_key_hash=OWNER_1,
            title="Empty",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await pg_repo.create_thread(thread)

        history = await pg_repo.list_messages(thread.id, OWNER_1)

        assert history == []

    @pytest.mark.asyncio
    async def test_repository_isolates_tenants(
        self,
        pg_repo: PostgresConversationRepository,
    ) -> None:
        thread = ConversationThread(
            id=str(uuid.uuid4()),
            owner_key_hash=OWNER_1,
            title="Private",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await pg_repo.create_thread(thread)

        assert await pg_repo.get_thread(thread.id, OWNER_2) is None
        assert await pg_repo.add_message(thread.id, OWNER_2, "user", "sneak") is None
        assert await pg_repo.list_messages(thread.id, OWNER_2) == []
        assert await pg_repo.list_messages(thread.id, OWNER_1) == []
