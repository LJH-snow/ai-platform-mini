import asyncio

from fastapi.testclient import TestClient

from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.core.container import provide_usage_service
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
