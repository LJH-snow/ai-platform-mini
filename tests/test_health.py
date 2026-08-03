import asyncio
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.core.container import provide_llm_provider, provide_usage_service
from app.exceptions.base import ProviderError
from app.main import app
from app.usage.memory_repository import InMemoryUsageRepository
from app.usage.models import UsageRecord
from app.usage.service import UsageService

client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_usage_returns_only_authenticated_key_summary() -> None:
    repository = InMemoryUsageRepository()
    usage_service = UsageService(repository)

    async def override_api_key() -> APIKey:
        return APIKey(key="hash1", name="key-1")

    app.dependency_overrides[require_api_key] = override_api_key
    app.dependency_overrides[provide_usage_service] = lambda: usage_service

    try:
        asyncio.run(
            usage_service.record(
                UsageRecord(
                    request_id="r1",
                    model="llama3",
                    total_tokens=30,
                    api_key_hash="hash1",
                )
            )
        )
        asyncio.run(
            usage_service.record(
                UsageRecord(
                    request_id="r2",
                    model="mistral",
                    total_tokens=300,
                    api_key_hash="hash2",
                )
            )
        )
        response = client.get("/api/v1/usage")
    finally:
        app.dependency_overrides.pop(require_api_key, None)
        app.dependency_overrides.pop(provide_usage_service, None)

    assert response.status_code == 200
    assert response.json()["total_requests"] == 1
    assert response.json()["total_tokens"] == 30
    assert set(response.json()["by_model"]) == {"llama3"}


# --- Fixture for readability tests using a mock provider ---


class _MockProvider:
    @property
    def default_model(self) -> str:
        return "mock-model"

    async def list_models(self) -> dict[str, object]:
        return {"models": [{"name": "mock-model"}]}

    async def close(self) -> None:
        pass


@pytest.fixture
def _override_provider() -> Generator[None, None, None]:
    app.dependency_overrides[provide_llm_provider] = lambda: _MockProvider()
    yield
    app.dependency_overrides.pop(provide_llm_provider, None)


# --- Readiness probe tests ---


def test_readiness_returns_200_when_provider_ok(
    _override_provider: None,
) -> None:
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"


def test_readiness_returns_503_when_provider_fails() -> None:
    class _FailingProvider:
        @property
        def default_model(self) -> str:
            return "fail-model"

        async def list_models(self) -> dict[str, object]:
            raise ProviderError("unavailable")

        async def close(self) -> None:
            pass

    app.dependency_overrides[provide_llm_provider] = lambda: _FailingProvider()
    try:
        response = client.get("/api/v1/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["provider"] == "failed"
    finally:
        app.dependency_overrides.pop(provide_llm_provider, None)


def test_readiness_memory_mode_no_database_check(
    _override_provider: None,
) -> None:
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    data = response.json()
    checks = data.get("checks", data)
    assert "database" not in checks
