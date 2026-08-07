import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient, Response

from app.auth.hash import hash_api_key
from app.conversations.memory_repository import InMemoryConversationRepository
from app.conversations.models import ConversationThread
from app.conversations.postgres_repository import PostgresConversationRepository
from app.conversations.service import ConversationService
from app.core.container import provide_conversation_service
from app.db.init import dispose_db, init_db
from app.db.session import create_async_session_factory
from app.exceptions.base import ConversationNotFoundError, ValidationError
from app.main import app
from app.schemas.chat import ChatMessage

OWNER_1 = hash_api_key("sk-owner-1")
OWNER_2 = hash_api_key("sk-owner-2")
_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}
_TEST_OWNER = hash_api_key("sk-test-integration")


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
async def test_memory_resolve_thread_creates_or_reuses() -> None:
    service = _service()

    created = await service.resolve_thread(OWNER_1, None, "New thread")

    assert created.id
    assert created.title == "New thread"
    fetched = await service.resolve_thread(OWNER_1, created.id, "Ignored title")
    assert fetched == created
    with pytest.raises(ConversationNotFoundError):
        await service.resolve_thread(OWNER_2, created.id, "Ignored title")


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
async def test_memory_merge_history_serves_server_first_and_deduplicates() -> None:
    service = _service()
    thread = await service.create_thread(OWNER_1, "Merge thread")
    await service.append_message(OWNER_1, thread.id, "user", "server user")
    await service.append_message(OWNER_1, thread.id, "assistant", "server answer")
    server_history = await service.load_history(OWNER_1, thread.id)

    merged = service.merge_history(
        server_history,
        [
            ChatMessage(role="user", content="server user"),
            ChatMessage(role="user", content="client only"),
        ],
    )

    assert [(message.role, message.content) for message in merged] == [
        ("user", "server user"),
        ("assistant", "server answer"),
        ("user", "client only"),
    ]


@pytest.mark.asyncio
async def test_memory_merge_history_drops_retried_current_user() -> None:
    service = _service()
    thread = await service.create_thread(OWNER_1, "Retry thread")
    await service.append_message(OWNER_1, thread.id, "user", "earlier")
    await service.append_message(OWNER_1, thread.id, "assistant", "ok")
    await service.append_message(OWNER_1, thread.id, "user", "question")
    await service.append_message(OWNER_1, thread.id, "assistant", "partial")
    server_history = await service.load_history(OWNER_1, thread.id)

    merged = service.merge_history(
        server_history,
        [
            ChatMessage(role="user", content="earlier"),
            ChatMessage(role="assistant", content="ok"),
            ChatMessage(role="user", content="question"),
        ],
        current_user_content="question",
    )

    assert [(message.role, message.content) for message in merged] == [
        ("user", "earlier"),
        ("assistant", "ok"),
    ]


@pytest.mark.asyncio
async def test_memory_merge_history_keeps_repeated_non_final_user() -> None:
    service = _service()
    thread = await service.create_thread(OWNER_1, "Repeat thread")
    await service.append_message(OWNER_1, thread.id, "user", "repeat")
    await service.append_message(OWNER_1, thread.id, "assistant", "ok")
    await service.append_message(OWNER_1, thread.id, "user", "next")
    server_history = await service.load_history(OWNER_1, thread.id)

    merged = service.merge_history(
        server_history,
        [],
        current_user_content="repeat",
    )

    assert [(message.role, message.content) for message in merged] == [
        ("user", "repeat"),
        ("assistant", "ok"),
        ("user", "next"),
    ]


@pytest.mark.asyncio
async def test_memory_persist_turn_appends_user_and_assistant() -> None:
    service = _service()
    thread = await service.create_thread(OWNER_1, "Turn thread")

    user_message, assistant_message = await service.persist_turn(
        OWNER_1, thread.id, "question", "answer"
    )

    assert user_message.role == "user"
    assert user_message.content == "question"
    assert assistant_message is not None
    assert assistant_message.role == "assistant"
    assert assistant_message.content == "answer"
    history = await service.load_history(OWNER_1, thread.id)
    assert [(message.role, message.content) for message in history] == [
        ("user", "question"),
        ("assistant", "answer"),
    ]

    user_only, no_assistant = await service.persist_turn(
        OWNER_1, thread.id, "second question"
    )
    assert user_only.content == "second question"
    assert no_assistant is None


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
async def test_memory_list_threads_filters_owner_and_sorts_by_updated_at() -> None:
    repo = InMemoryConversationRepository()
    older = ConversationThread(
        id=str(uuid.uuid4()),
        owner_key_hash=OWNER_1,
        title="Older",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        updated_at=datetime(2026, 8, 2, tzinfo=UTC),
    )
    recent = ConversationThread(
        id=str(uuid.uuid4()),
        owner_key_hash=OWNER_1,
        title="Recent",
        created_at=datetime(2026, 8, 3, tzinfo=UTC),
        updated_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    foreign = ConversationThread(
        id=str(uuid.uuid4()),
        owner_key_hash=OWNER_2,
        title="Foreign",
        created_at=datetime(2026, 8, 3, tzinfo=UTC),
        updated_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    await repo.create_thread(older)
    await repo.create_thread(recent)
    await repo.create_thread(foreign)

    threads = await repo.list_threads(OWNER_1)

    assert [thread.id for thread in threads] == [recent.id, older.id]
    assert all(thread.owner_key_hash == OWNER_1 for thread in threads)


@pytest.mark.asyncio
async def test_service_list_threads_validates_owner() -> None:
    service = _service()

    with pytest.raises(ValueError, match="SHA-256"):
        await service.list_threads("not-a-hash")


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


@pytest.mark.asyncio
async def test_service_resolve_thread_falls_back_for_blank_title() -> None:
    service = _service()

    thread = await service.resolve_thread(OWNER_1, None, "   ")

    assert thread.title == "New conversation"


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

    @pytest.mark.asyncio
    async def test_list_threads_orders_most_recent_first(
        self,
        pg_repo: PostgresConversationRepository,
    ) -> None:
        older = ConversationThread(
            id=str(uuid.uuid4()),
            owner_key_hash=OWNER_1,
            title="Older",
            created_at=datetime(2026, 8, 1, tzinfo=UTC),
            updated_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        recent = ConversationThread(
            id=str(uuid.uuid4()),
            owner_key_hash=OWNER_1,
            title="Recent",
            created_at=datetime(2026, 8, 3, tzinfo=UTC),
            updated_at=datetime(2026, 8, 4, tzinfo=UTC),
        )
        foreign = ConversationThread(
            id=str(uuid.uuid4()),
            owner_key_hash=OWNER_2,
            title="Foreign",
            created_at=datetime(2026, 8, 3, tzinfo=UTC),
            updated_at=datetime(2026, 8, 4, tzinfo=UTC),
        )
        await pg_repo.create_thread(older)
        await pg_repo.create_thread(recent)
        await pg_repo.create_thread(foreign)

        threads = await pg_repo.list_threads(OWNER_1)

        assert [thread.id for thread in threads] == [recent.id, older.id]
        assert all(thread.owner_key_hash == OWNER_1 for thread in threads)

    @pytest.mark.asyncio
    async def test_invalid_uuid_thread_is_treated_as_missing(
        self,
        pg_repo: PostgresConversationRepository,
    ) -> None:
        canonical = str(uuid.uuid4())
        invalid_ids = [
            "not-a-uuid",
            f"{{{canonical}}}",
            f"urn:uuid:{canonical}",
        ]

        for thread_id in invalid_ids:
            assert await pg_repo.get_thread(thread_id, OWNER_1) is None
            assert (
                await pg_repo.add_message(thread_id, OWNER_1, "user", "hello") is None
            )
            assert await pg_repo.list_messages(thread_id, OWNER_1) == []

    @pytest.mark.asyncio
    async def test_non_canonical_uuid_forms_resolve_to_existing_thread(
        self,
        pg_repo: PostgresConversationRepository,
    ) -> None:
        thread = ConversationThread(
            id=str(uuid.uuid4()),
            owner_key_hash=OWNER_1,
            title="Canonical",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await pg_repo.create_thread(thread)
        compact = thread.id.replace("-", "")

        resolved = await pg_repo.get_thread(compact, OWNER_1)
        assert resolved == thread
        assert (
            await pg_repo.add_message(compact, OWNER_1, "user", "hello")
        ) is not None
        history = await pg_repo.list_messages(compact, OWNER_1)
        assert [message.content for message in history] == ["hello"]


class TestPostgresMessagesApi:
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
            yield PostgresConversationRepository(factory)
            await dispose_db()

    @pytest.mark.asyncio
    async def test_messages_api_lists_postgres_history(
        self,
        pg_repo: PostgresConversationRepository,
    ) -> None:
        service = ConversationService(repository=pg_repo)
        app.dependency_overrides[provide_conversation_service] = lambda: service
        response: Response | None = None
        try:
            thread = await service.create_thread(_TEST_OWNER, "PG history API")
            await service.append_message(
                _TEST_OWNER, thread.id, "user", "hello", token_count=3
            )
            await service.append_message(
                _TEST_OWNER, thread.id, "assistant", "hi", token_count=5
            )
            await service.append_message(
                _TEST_OWNER, thread.id, "user", "again", token_count=2
            )
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as http:
                response = await http.get(
                    f"/api/v1/conversations/{thread.id}/messages",
                    headers=_AUTH_HEADERS,
                )
        finally:
            app.dependency_overrides.pop(provide_conversation_service, None)

        assert response is not None
        assert response.status_code == 200
        body = response.json()
        assert [message["content"] for message in body] == ["hello", "hi", "again"]
        assert [message["token_count"] for message in body] == [3, 5, 2]
        assert all(message["thread_id"] == thread.id for message in body)
