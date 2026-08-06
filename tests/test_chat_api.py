import asyncio
import uuid

from fastapi.testclient import TestClient

from app.api.chat import get_chat_service
from app.auth.dependencies import provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKeyRecord
from app.auth.service import APIKeyService
from app.conversations.memory_repository import InMemoryConversationRepository
from app.conversations.service import ConversationService
from app.core.container import provide_conversation_service
from app.exceptions.ollama import OllamaModelNotFoundError, OllamaServiceError
from app.main import app
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse

client = TestClient(app)

_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}
_TEST_OWNER = hash_api_key("sk-test-integration")


def test_chat_openapi_documents_default_model_routing_priority() -> None:
    operation = app.openapi()["paths"]["/api/v1/chat"]["post"]
    description = operation["description"]

    assert "default model always uses the default provider" in description
    assert "Remaining gpt-* models route to OpenAI" in description


def test_chat_request_model_schema_documents_default_model_priority() -> None:
    model_schema = ChatRequest.model_json_schema()["properties"]["model"]
    description = model_schema["description"]

    assert "default model always uses the default provider" in description
    assert "remaining gpt-* models route to OpenAI" in description
    assert "OLLAMA_DEFAULT_MODEL" in description


def test_generated_request_id_is_full_uuid4_hex() -> None:
    response = client.get("/api/v1/health")

    request_id = response.headers["X-Request-ID"]
    parsed = uuid.UUID(hex=request_id)
    assert parsed.version == 4
    assert parsed.hex == request_id


class SuccessfulChatService:
    async def chat(self, request: ChatRequest) -> ChatResponse:
        assert request.message == "Hello"
        return ChatResponse(
            model="llama3.2",
            created_at="2026-07-31T00:00:00Z",
            message=ChatMessage(role="assistant", content="Hi there."),
            done=True,
            done_reason="stop",
        )


class FailingChatService:
    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise OllamaServiceError("Unable to reach Ollama at http://localhost:11434.")


class MissingModelChatService:
    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise OllamaModelNotFoundError("model 'llama3.2' not found")


class RecordingChatService:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(
            model="llama3.2",
            thread_id=request.thread_id,
            created_at="2026-07-31T00:00:00Z",
            message=ChatMessage(role="assistant", content="Hi there."),
            done=True,
            done_reason="stop",
        )


def _memory_conversation_service() -> ConversationService:
    return ConversationService(repository=InMemoryConversationRepository())


def test_chat_endpoint_returns_model_reply() -> None:
    async def override_service() -> SuccessfulChatService:
        return SuccessfulChatService()

    app.dependency_overrides[get_chat_service] = override_service

    try:
        response = client.post(
            "/api/v1/chat",
            json={"message": "Hello", "history": [{"role": "user", "content": "Hi"}]},
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "llama3.2"
    assert body["created_at"] == "2026-07-31T00:00:00Z"
    assert body["message"] == {"role": "assistant", "content": "Hi there."}
    assert body["done"] is True
    assert body["done_reason"] == "stop"


def test_chat_endpoint_creates_thread_and_persists_turn() -> None:
    conversation_service = _memory_conversation_service()
    chat_service = RecordingChatService()
    app.dependency_overrides[get_chat_service] = lambda: chat_service
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )

    try:
        response = client.post(
            "/api/v1/chat",
            json={
                "message": "Hello",
                "history": [{"role": "user", "content": "client old"}],
            },
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"]
    assert body["message"] == {"role": "assistant", "content": "Hi there."}
    history = asyncio.run(
        conversation_service.load_history(_TEST_OWNER, body["thread_id"])
    )
    assert [(message.role, message.content) for message in history] == [
        ("user", "Hello"),
        ("assistant", "Hi there."),
    ]
    assert [
        (message.role, message.content) for message in chat_service.requests[0].history
    ] == [("user", "client old")]


def test_chat_endpoint_reuses_thread_and_merges_server_history_first() -> None:
    conversation_service = _memory_conversation_service()
    chat_service = RecordingChatService()
    app.dependency_overrides[get_chat_service] = lambda: chat_service
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )

    try:
        first = client.post(
            "/api/v1/chat",
            json={"message": "First"},
            headers=_AUTH_HEADERS,
        )
        thread_id = first.json()["thread_id"]
        second = client.post(
            "/api/v1/chat",
            json={
                "message": "Follow up",
                "thread_id": thread_id,
                "history": [
                    {"role": "user", "content": "First"},
                    {"role": "assistant", "content": "Hi there."},
                    {"role": "user", "content": "client only"},
                ],
            },
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["thread_id"] == thread_id
    assert [
        (message.role, message.content) for message in chat_service.requests[1].history
    ] == [
        ("user", "First"),
        ("assistant", "Hi there."),
        ("user", "client only"),
    ]
    history = asyncio.run(conversation_service.load_history(_TEST_OWNER, thread_id))
    assert [(message.role, message.content) for message in history] == [
        ("user", "First"),
        ("assistant", "Hi there."),
        ("user", "Follow up"),
        ("assistant", "Hi there."),
    ]


def test_chat_endpoint_isolates_foreign_threads() -> None:
    conversation_service = _memory_conversation_service()
    chat_service = RecordingChatService()
    auth_service = APIKeyService(
        repository=InMemoryAPIKeyRepository(
            [
                APIKeyRecord(
                    key_hash=hash_api_key("sk-test-integration"),
                    name="one",
                    status="active",
                ),
                APIKeyRecord(
                    key_hash=hash_api_key("sk-other"),
                    name="two",
                    status="active",
                ),
            ]
        )
    )
    app.dependency_overrides[get_chat_service] = lambda: chat_service
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )
    app.dependency_overrides[provide_api_key_service] = lambda: auth_service

    try:
        first = client.post(
            "/api/v1/chat",
            json={"message": "Private"},
            headers=_AUTH_HEADERS,
        )
        thread_id = first.json()["thread_id"]
        response = client.post(
            "/api/v1/chat",
            json={"message": "Sneak", "thread_id": thread_id},
            headers={"Authorization": "Bearer sk-other"},
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)
        app.dependency_overrides.pop(provide_conversation_service, None)
        app.dependency_overrides.pop(provide_api_key_service, None)

    assert response.status_code == 404
    assert response.json()["code"] == "CONVERSATION_NOT_FOUND"


def test_chat_endpoint_returns_not_found_for_invalid_thread_id() -> None:
    conversation_service = _memory_conversation_service()
    chat_service = RecordingChatService()
    app.dependency_overrides[get_chat_service] = lambda: chat_service
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )

    canonical = str(uuid.uuid4())
    invalid_thread_ids = [
        "not-a-uuid",
        f"{{{canonical}}}",
        f"urn:uuid:{canonical}",
    ]

    try:
        for thread_id in invalid_thread_ids:
            response = client.post(
                "/api/v1/chat",
                json={"message": "Hello", "thread_id": thread_id},
                headers=_AUTH_HEADERS,
            )
            assert response.status_code == 404
            assert response.json()["code"] == "CONVERSATION_NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_chat_service, None)
        app.dependency_overrides.pop(provide_conversation_service, None)


def test_chat_endpoint_retry_does_not_duplicate_current_user() -> None:
    conversation_service = _memory_conversation_service()
    chat_service = RecordingChatService()
    app.dependency_overrides[get_chat_service] = lambda: chat_service
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )

    try:
        first = client.post(
            "/api/v1/chat",
            json={"message": "Hello"},
            headers=_AUTH_HEADERS,
        )
        thread_id = first.json()["thread_id"]
        second = client.post(
            "/api/v1/chat",
            json={
                "message": "Hello",
                "thread_id": thread_id,
                "history": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there."},
                ],
            },
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert first.status_code == 200
    assert second.status_code == 200
    assert chat_service.requests[1].message == "Hello"
    assert [
        (message.role, message.content) for message in chat_service.requests[1].history
    ] == []


def test_chat_endpoint_blank_message_uses_fallback_title() -> None:
    conversation_service = _memory_conversation_service()
    chat_service = RecordingChatService()
    app.dependency_overrides[get_chat_service] = lambda: chat_service
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )

    try:
        response = client.post(
            "/api/v1/chat",
            json={"message": "   "},
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert response.status_code == 200
    thread_id = response.json()["thread_id"]
    thread = asyncio.run(conversation_service.get_thread(_TEST_OWNER, thread_id))
    assert thread.title == "New conversation"


def test_chat_endpoint_maps_ollama_errors_to_bad_gateway() -> None:
    async def override_service() -> FailingChatService:
        return FailingChatService()

    app.dependency_overrides[get_chat_service] = override_service

    try:
        response = client.post(
            "/api/v1/chat",
            json={"message": "Hello"},
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)

    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "PROVIDER_ERROR"
    assert body["message"] == "Unable to reach Ollama at http://localhost:11434."
    assert "request_id" in body


def test_chat_endpoint_error_response_carries_created_thread_id() -> None:
    conversation_service = _memory_conversation_service()
    app.dependency_overrides[get_chat_service] = lambda: FailingChatService()
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )

    try:
        response = client.post(
            "/api/v1/chat",
            json={"message": "Hello"},
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "PROVIDER_ERROR"
    assert body["thread_id"]
    history = asyncio.run(
        conversation_service.load_history(_TEST_OWNER, body["thread_id"])
    )
    assert history == []


def test_chat_endpoint_maps_missing_model_to_not_found() -> None:
    async def override_service() -> MissingModelChatService:
        return MissingModelChatService()

    app.dependency_overrides[get_chat_service] = override_service

    try:
        response = client.post(
            "/api/v1/chat",
            json={"message": "Hello"},
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "MODEL_NOT_FOUND"
    assert body["message"] == "model 'llama3.2' not found"
    assert "request_id" in body
