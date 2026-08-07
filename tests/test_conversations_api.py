import asyncio
import uuid
from urllib.parse import quote

from fastapi.testclient import TestClient

from app.auth.dependencies import provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKeyRecord
from app.auth.service import APIKeyService
from app.conversations.memory_repository import InMemoryConversationRepository
from app.conversations.models import ConversationMessage, ConversationThread
from app.conversations.service import ConversationService
from app.core.container import provide_conversation_service
from app.main import app

client = TestClient(app)

_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}
_TEST_OWNER = hash_api_key("sk-test-integration")
_OTHER_KEY = "sk-other"
_OTHER_OWNER = hash_api_key(_OTHER_KEY)


def _memory_conversation_service() -> ConversationService:
    return ConversationService(repository=InMemoryConversationRepository())


def _override_conversation_service(service: ConversationService) -> None:
    app.dependency_overrides[provide_conversation_service] = lambda: service


def _clear_conversation_service_override() -> None:
    app.dependency_overrides.pop(provide_conversation_service, None)


def _two_key_auth_service() -> APIKeyService:
    return APIKeyService(
        repository=InMemoryAPIKeyRepository(
            [
                APIKeyRecord(
                    key_hash=_TEST_OWNER,
                    name="one",
                    status="active",
                ),
                APIKeyRecord(
                    key_hash=_OTHER_OWNER,
                    name="two",
                    status="active",
                ),
            ]
        )
    )


async def _seed_history(
    service: ConversationService,
    owner_key_hash: str,
    title: str,
) -> tuple[
    ConversationThread,
    ConversationMessage,
    ConversationMessage,
    ConversationMessage,
]:
    thread = await service.create_thread(owner_key_hash, title)
    first = await service.append_message(
        owner_key_hash, thread.id, "user", "hello", token_count=3
    )
    second = await service.append_message(
        owner_key_hash, thread.id, "assistant", "hi", token_count=5
    )
    third = await service.append_message(
        owner_key_hash, thread.id, "user", "again", token_count=2
    )
    return thread, first, second, third


def test_messages_endpoint_requires_authentication() -> None:
    response = client.get(f"/api/v1/conversations/{uuid.uuid4()}/messages")

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_ERROR"


def test_conversations_endpoint_requires_authentication() -> None:
    response = client.get("/api/v1/conversations")

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_ERROR"


def test_conversations_endpoint_returns_empty_list() -> None:
    service = _memory_conversation_service()
    _override_conversation_service(service)
    try:
        response = client.get("/api/v1/conversations", headers=_AUTH_HEADERS)
    finally:
        _clear_conversation_service_override()

    assert response.status_code == 200
    assert response.json() == []


def test_conversations_endpoint_lists_owned_threads_most_recent_first() -> None:
    service = _memory_conversation_service()
    _override_conversation_service(service)
    try:

        async def _seed() -> tuple[
            ConversationThread, ConversationThread, ConversationThread
        ]:
            older = await service.create_thread(_TEST_OWNER, "Older")
            await service.append_message(
                _TEST_OWNER, older.id, "user", "older question"
            )
            recent = await service.create_thread(_TEST_OWNER, "Recent")
            await service.append_message(
                _TEST_OWNER, recent.id, "user", "recent question"
            )
            foreign = await service.create_thread(_OTHER_OWNER, "Foreign")
            return older, recent, foreign

        older, recent, foreign = asyncio.run(_seed())
        response = client.get("/api/v1/conversations", headers=_AUTH_HEADERS)
    finally:
        _clear_conversation_service_override()

    assert response.status_code == 200
    body = response.json()
    assert [item["thread_id"] for item in body] == [recent.id, older.id]
    assert foreign.id not in [item["thread_id"] for item in body]
    assert set(body[0]) == {
        "thread_id",
        "title",
        "created_at",
        "updated_at",
    }
    assert body[0]["title"] == "Recent"


def test_messages_endpoint_returns_empty_list_for_empty_thread() -> None:
    service = _memory_conversation_service()
    _override_conversation_service(service)
    try:
        thread = asyncio.run(service.create_thread(_TEST_OWNER, "Empty thread"))
        response = client.get(
            f"/api/v1/conversations/{thread.id}/messages",
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_conversation_service_override()

    assert response.status_code == 200
    assert response.json() == []


def test_messages_endpoint_returns_all_messages_in_order() -> None:
    service = _memory_conversation_service()
    _override_conversation_service(service)
    try:
        thread, first, second, third = asyncio.run(
            _seed_history(service, _TEST_OWNER, "History thread")
        )
        response = client.get(
            f"/api/v1/conversations/{thread.id}/messages",
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_conversation_service_override()

    assert response.status_code == 200
    body = response.json()
    assert [message["role"] for message in body] == ["user", "assistant", "user"]
    assert [message["content"] for message in body] == ["hello", "hi", "again"]
    assert [message["token_count"] for message in body] == [3, 5, 2]
    assert [message["id"] for message in body] == [first.id, second.id, third.id]
    assert [message["thread_id"] for message in body] == [thread.id] * 3
    assert set(body[0]) == {
        "id",
        "thread_id",
        "role",
        "content",
        "token_count",
        "created_at",
    }
    assert all(message["created_at"] for message in body)


def test_messages_endpoint_returns_not_found_for_missing_thread() -> None:
    response = client.get(
        f"/api/v1/conversations/{uuid.uuid4()}/messages",
        headers=_AUTH_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CONVERSATION_NOT_FOUND"


def test_messages_endpoint_returns_not_found_for_foreign_tenant() -> None:
    service = _memory_conversation_service()
    _override_conversation_service(service)
    app.dependency_overrides[provide_api_key_service] = lambda: _two_key_auth_service()
    try:
        thread = asyncio.run(service.create_thread(_TEST_OWNER, "Private"))
        response = client.get(
            f"/api/v1/conversations/{thread.id}/messages",
            headers={"Authorization": f"Bearer {_OTHER_KEY}"},
        )
    finally:
        _clear_conversation_service_override()
        app.dependency_overrides.pop(provide_api_key_service, None)

    assert response.status_code == 404
    assert response.json()["code"] == "CONVERSATION_NOT_FOUND"


def test_messages_endpoint_returns_not_found_for_invalid_thread_ids() -> None:
    service = _memory_conversation_service()
    _override_conversation_service(service)
    canonical = str(uuid.uuid4())
    try:
        for thread_id in ("not-a-uuid", f"{{{canonical}}}", f"urn:uuid:{canonical}"):
            encoded = quote(thread_id, safe="")
            response = client.get(
                f"/api/v1/conversations/{encoded}/messages",
                headers=_AUTH_HEADERS,
            )
            assert response.status_code == 404
            assert response.json()["code"] == "CONVERSATION_NOT_FOUND"
    finally:
        _clear_conversation_service_override()
