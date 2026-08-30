import asyncio
import uuid

from fastapi.testclient import TestClient

from app.auth.dependencies import provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKeyRecord
from app.auth.service import APIKeyService
from app.core.container import provide_memory_service
from app.main import app
from app.memory.memory_repository import InMemoryMemoryRepository
from app.memory.service import MemoryService

client = TestClient(app)

_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}
_TEST_OWNER = hash_api_key("sk-test-integration")
_OTHER_KEY = "sk-other"
_OTHER_OWNER = hash_api_key(_OTHER_KEY)


def _memory_service() -> MemoryService:
    return MemoryService(repository=InMemoryMemoryRepository())


def _override_memory_service(service: MemoryService) -> None:
    app.dependency_overrides[provide_memory_service] = lambda: service


def _clear_memory_service_override() -> None:
    app.dependency_overrides.pop(provide_memory_service, None)


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


def test_memory_endpoint_requires_authentication() -> None:
    response = client.get("/api/v1/memory")

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_ERROR"


def test_memory_crud_flow() -> None:
    service = _memory_service()
    _override_memory_service(service)
    try:
        created = client.post(
            "/api/v1/memory",
            headers=_AUTH_HEADERS,
            json={
                "content": "用户偏好中文回答",
                "kind": "preference",
                "confidence": 0.95,
                "metadata": {"channel": "api"},
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["content"] == "用户偏好中文回答"
        assert body["kind"] == "preference"
        assert body["confidence"] == 0.95
        assert body["metadata"] == {"channel": "api"}
        memory_id = body["id"]

        listed = client.get("/api/v1/memory?q=中文", headers=_AUTH_HEADERS)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [memory_id]

        fetched = client.get(f"/api/v1/memory/{memory_id}", headers=_AUTH_HEADERS)
        assert fetched.status_code == 200
        assert fetched.json()["id"] == memory_id

        updated = client.patch(
            f"/api/v1/memory/{memory_id}",
            headers=_AUTH_HEADERS,
            json={"kind": "instruction", "content": "必须使用中文回答"},
        )
        assert updated.status_code == 200
        assert updated.json()["kind"] == "instruction"
        assert updated.json()["content"] == "必须使用中文回答"

        deleted = client.delete(f"/api/v1/memory/{memory_id}", headers=_AUTH_HEADERS)
        assert deleted.status_code == 204

        missing = client.get(f"/api/v1/memory/{memory_id}", headers=_AUTH_HEADERS)
        assert missing.status_code == 404
        assert missing.json()["code"] == "MEMORY_NOT_FOUND"
    finally:
        _clear_memory_service_override()


def test_memory_endpoint_isolates_legacy_keys() -> None:
    service = _memory_service()
    _override_memory_service(service)
    app.dependency_overrides[provide_api_key_service] = lambda: _two_key_auth_service()
    try:
        owned = asyncio.run(service.create_memory(_TEST_OWNER, "private fact"))
        foreign = client.get(
            f"/api/v1/memory/{owned.id}",
            headers={"Authorization": f"Bearer {_OTHER_KEY}"},
        )
        assert foreign.status_code == 404

        listed = client.get(
            "/api/v1/memory",
            headers={"Authorization": f"Bearer {_OTHER_KEY}"},
        )
        assert listed.status_code == 200
        assert listed.json() == []
    finally:
        _clear_memory_service_override()
        app.dependency_overrides.pop(provide_api_key_service, None)


def test_memory_endpoint_returns_not_found_for_invalid_ids() -> None:
    service = _memory_service()
    _override_memory_service(service)
    try:
        response = client.get(
            f"/api/v1/memory/{uuid.uuid4()}",
            headers=_AUTH_HEADERS,
        )
        assert response.status_code == 404
        assert response.json()["code"] == "MEMORY_NOT_FOUND"
    finally:
        _clear_memory_service_override()


def test_memory_endpoint_validates_create_inputs() -> None:
    _override_memory_service(_memory_service())
    try:
        response = client.post(
            "/api/v1/memory",
            headers=_AUTH_HEADERS,
            json={"content": "", "kind": "not-a-kind"},
        )
        assert response.status_code == 422
    finally:
        _clear_memory_service_override()
