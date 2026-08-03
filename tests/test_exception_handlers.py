import asyncio

from fastapi.testclient import TestClient

from app.api.chat import get_chat_service
from app.core.container import provide_quota_service
from app.exceptions.base import ProviderError, ProviderUnavailableError
from app.exceptions.ollama import OllamaModelNotFoundError
from app.main import app
from app.quota.memory_repository import InMemoryQuotaRepository
from app.quota.models import QuotaConfig
from app.quota.service import QuotaService
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse
from app.usage.memory_repository import InMemoryUsageRepository

client = TestClient(app)

_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}


class UnavailableChatService:
    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise ProviderUnavailableError("Provider is down")


class GenericErrorChatService:
    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise ProviderError("Something went wrong")


class SlowChatService:
    async def chat(self, request: ChatRequest) -> ChatResponse:
        await asyncio.sleep(1)
        return ChatResponse(
            model="mock-model",
            created_at=None,
            message=ChatMessage(role="assistant", content="unused"),
            done=True,
            done_reason="stop",
        )


class NonRenewableQuotaService(QuotaService):
    @property
    def reservation_renewal_seconds(self) -> int:
        return 0

    async def renew(self, reservation_id: str) -> bool:
        return False


def test_provider_unavailable_returns_502() -> None:
    async def override() -> UnavailableChatService:
        return UnavailableChatService()

    app.dependency_overrides[get_chat_service] = override

    try:
        response = client.post(
            "/api/v1/chat",
            json={"message": "Hi"},
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)

    assert response.status_code == 502
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_provider_error_returns_502() -> None:
    async def override() -> GenericErrorChatService:
        return GenericErrorChatService()

    app.dependency_overrides[get_chat_service] = override

    try:
        response = client.post(
            "/api/v1/chat",
            json={"message": "Hi"},
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)

    assert response.status_code == 502
    assert response.json()["code"] == "PROVIDER_ERROR"


def test_quota_renewal_failure_returns_503() -> None:
    usage_repository = InMemoryUsageRepository()
    quota_service = NonRenewableQuotaService(
        usage_repository=usage_repository,
        quota_repository=InMemoryQuotaRepository(usage_repository),
        config=QuotaConfig(daily_token_limit=100, default_reserve_tokens=50),
    )

    async def override_service() -> SlowChatService:
        return SlowChatService()

    app.dependency_overrides[get_chat_service] = override_service
    app.dependency_overrides[provide_quota_service] = lambda: quota_service

    try:
        response = client.post(
            "/api/v1/chat",
            json={"message": "Hi"},
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)
        app.dependency_overrides.pop(provide_quota_service, None)

    assert response.status_code == 503
    assert response.json()["code"] == "QUOTA_UNAVAILABLE"


def test_model_not_found_returns_404() -> None:
    class MissingModelService:
        async def chat(self, request: ChatRequest) -> ChatResponse:
            raise OllamaModelNotFoundError("model not found")

    async def override() -> MissingModelService:
        return MissingModelService()

    app.dependency_overrides[get_chat_service] = override

    try:
        response = client.post(
            "/api/v1/chat",
            json={"message": "Hi"},
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)

    assert response.status_code == 404
    assert response.json()["code"] == "MODEL_NOT_FOUND"


def test_validation_error_on_invalid_temperature() -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "Hi"}],
            "temperature": 999,
        },
        headers=_AUTH_HEADERS,
    )
    assert response.status_code == 422


def test_validation_error_on_empty_messages() -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"messages": []},
        headers=_AUTH_HEADERS,
    )
    assert response.status_code == 422
