from fastapi.testclient import TestClient

from app.api.chat import get_chat_service
from app.exceptions.base import ProviderError, ProviderUnavailableError
from app.exceptions.ollama import OllamaModelNotFoundError
from app.main import app
from app.schemas.chat import ChatRequest, ChatResponse

client = TestClient(app)


class UnavailableChatService:
    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise ProviderUnavailableError("Provider is down")


class GenericErrorChatService:
    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise ProviderError("Something went wrong")


def test_provider_unavailable_returns_502() -> None:
    async def override() -> UnavailableChatService:
        return UnavailableChatService()

    app.dependency_overrides[get_chat_service] = override

    try:
        response = client.post("/api/v1/chat", json={"message": "Hi"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_provider_error_returns_502() -> None:
    async def override() -> GenericErrorChatService:
        return GenericErrorChatService()

    app.dependency_overrides[get_chat_service] = override

    try:
        response = client.post("/api/v1/chat", json={"message": "Hi"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["code"] == "PROVIDER_ERROR"


def test_model_not_found_returns_404() -> None:
    class MissingModelService:
        async def chat(self, request: ChatRequest) -> ChatResponse:
            raise OllamaModelNotFoundError("model not found")

    async def override() -> MissingModelService:
        return MissingModelService()

    app.dependency_overrides[get_chat_service] = override

    try:
        response = client.post("/api/v1/chat", json={"message": "Hi"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["code"] == "MODEL_NOT_FOUND"


def test_validation_error_on_invalid_temperature() -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "Hi"}],
            "temperature": 999,
        },
    )
    assert response.status_code == 422


def test_validation_error_on_empty_messages() -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"messages": []},
    )
    assert response.status_code == 422
