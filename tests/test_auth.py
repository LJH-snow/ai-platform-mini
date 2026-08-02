from fastapi.testclient import TestClient

from app.auth.dependencies import provide_api_key_service
from app.auth.models import APIKey
from app.auth.service import APIKeyService
from app.main import app

client = TestClient(app)


def test_no_key_returns_401_when_keys_configured() -> None:
    service = APIKeyService(api_keys=[APIKey(key="sk-test", name="test")])

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
    service = APIKeyService(api_keys=[APIKey(key="sk-test", name="test")])

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
    service = APIKeyService(api_keys=[APIKey(key="sk-test", name="test")])

    def override() -> APIKeyService:
        return service

    app.dependency_overrides[provide_api_key_service] = override

    try:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}]},
            headers={"Authorization": "Bearer sk-test"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code != 401


def test_no_keys_configured_allows_anonymous() -> None:
    service = APIKeyService(api_keys=[])

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

    assert response.status_code != 401


def test_health_endpoint_does_not_require_auth() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_ready_endpoint_does_not_require_auth() -> None:
    response = client.get("/api/v1/ready")
    assert response.status_code in (200, 503)
