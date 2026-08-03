import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKeyRecord
from app.auth.service import APIKeyService
from app.main import app

client = TestClient(app)

_RAW_KEY = "sk-test-key-12345"


def _make_service(*raw_keys: str) -> APIKeyService:
    records = [
        APIKeyRecord(key_hash=hash_api_key(k), name=k[:8], status="active")
        for k in raw_keys
    ]
    repository = InMemoryAPIKeyRepository(records)
    return APIKeyService(repository=repository)


def test_no_key_returns_401_when_auth_enabled() -> None:
    service = _make_service(_RAW_KEY)

    def override() -> APIKeyService:
        return service

    app.dependency_overrides[provide_api_key_service] = override

    try:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_ERROR"


def test_invalid_key_returns_401() -> None:
    service = _make_service(_RAW_KEY)

    def override() -> APIKeyService:
        return service

    app.dependency_overrides[provide_api_key_service] = override

    try:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}]},
            headers={"Authorization": "Bearer sk-wrong"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_ERROR"


def test_valid_key_passes_auth() -> None:
    service = _make_service(_RAW_KEY)

    def override() -> APIKeyService:
        return service

    app.dependency_overrides[provide_api_key_service] = override

    try:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}]},
            headers={"Authorization": f"Bearer {_RAW_KEY}"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code != 401


def test_empty_keys_returns_401_when_auth_enabled() -> None:
    service = _make_service()

    def override() -> APIKeyService:
        return service

    app.dependency_overrides[provide_api_key_service] = override

    try:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_auth_disabled_allows_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    get_settings.cache_clear()

    try:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )
    finally:
        get_settings.cache_clear()

    assert response.status_code != 401


def test_health_endpoint_does_not_require_auth() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_ready_endpoint_does_not_require_auth() -> None:
    response = client.get("/api/v1/ready")
    assert response.status_code in (200, 503)
