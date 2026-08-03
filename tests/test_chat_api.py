import uuid

from fastapi.testclient import TestClient

from app.api.chat import get_chat_service
from app.exceptions.ollama import OllamaModelNotFoundError, OllamaServiceError
from app.main import app
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse

client = TestClient(app)

_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}


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
