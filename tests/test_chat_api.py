from fastapi.testclient import TestClient

from app.api.chat import get_ollama_service
from app.main import app
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse
from app.services.ollama_service import OllamaServiceError

client = TestClient(app)


class SuccessfulOllamaService:
    async def chat(self, request: ChatRequest) -> ChatResponse:
        assert request.message == "Hello"
        return ChatResponse(
            model="llama3.2",
            created_at="2026-07-31T00:00:00Z",
            message=ChatMessage(role="assistant", content="Hi there."),
            done=True,
            done_reason="stop",
        )


class FailingOllamaService:
    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise OllamaServiceError("Unable to reach Ollama at http://localhost:11434.")


def test_chat_endpoint_returns_model_reply() -> None:
    async def override_service() -> SuccessfulOllamaService:
        return SuccessfulOllamaService()

    app.dependency_overrides[get_ollama_service] = override_service

    try:
        response = client.post(
            "/api/chat",
            json={"message": "Hello", "history": [{"role": "user", "content": "Hi"}]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "model": "llama3.2",
        "created_at": "2026-07-31T00:00:00Z",
        "message": {"role": "assistant", "content": "Hi there."},
        "done": True,
        "done_reason": "stop",
    }


def test_chat_endpoint_maps_ollama_errors_to_bad_gateway() -> None:
    async def override_service() -> FailingOllamaService:
        return FailingOllamaService()

    app.dependency_overrides[get_ollama_service] = override_service

    try:
        response = client.post("/api/chat", json={"message": "Hello"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Unable to reach Ollama at http://localhost:11434."
    }
