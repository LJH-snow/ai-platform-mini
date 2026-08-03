from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import _admin_key_hashes, provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKeyRecord
from app.auth.service import APIKeyService
from app.core.settings import get_settings
from app.main import app

_ADMIN_KEY = "sk-admin-test"
_ADMIN_HASH = hash_api_key(_ADMIN_KEY)
_REGULAR_KEY = "sk-regular-test"


def _make_service(*, extra_records: list[APIKeyRecord] | None = None) -> APIKeyService:
    records = [
        APIKeyRecord(key_hash=_ADMIN_HASH, name="admin", status="active"),
        APIKeyRecord(
            key_hash=hash_api_key(_REGULAR_KEY),
            name="regular",
            status="active",
        ),
    ]
    if extra_records:
        records.extend(extra_records)
    return APIKeyService(repository=InMemoryAPIKeyRepository(records))


client = TestClient(app)


@pytest.fixture(autouse=True)
def _setup_admin(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("ADMIN_API_KEYS", _ADMIN_KEY)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    _admin_key_hashes.cache_clear()
    app.dependency_overrides[provide_api_key_service] = lambda: _make_service()
    yield
    app.dependency_overrides = {}
    get_settings.cache_clear()
    _admin_key_hashes.cache_clear()


def test_create_api_key_returns_raw_key_once() -> None:
    response = client.post(
        "/admin/api-keys",
        json={"name": "new-key"},
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "new-key"
    assert data["raw_key"].startswith("sk-")
    assert len(data["raw_key"]) > 10
    assert data["created_at"] is not None


def test_list_api_keys_excludes_hash() -> None:
    response = client.get(
        "/admin/api-keys",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 200
    keys = response.json()
    assert len(keys) >= 2
    for k in keys:
        assert "key_hash" not in k
        assert "key_hash_prefix" in k
        assert "name" in k
        assert "status" in k


def test_revoke_api_key_by_prefix() -> None:
    prefix = hash_api_key(_REGULAR_KEY)[:8]
    response = client.delete(
        f"/admin/api-keys/{prefix}",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["revoked"] is True


def test_revoke_invalid_prefix_returns_422() -> None:
    response = client.delete(
        "/admin/api-keys/abc",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 422


def test_revoke_short_prefix_returns_422() -> None:
    response = client.delete(
        "/admin/api-keys/abcd123",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 422


def test_revoke_uppercase_prefix_returns_422() -> None:
    prefix = hash_api_key(_REGULAR_KEY)[:8].upper()
    response = client.delete(
        f"/admin/api-keys/{prefix}",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 422


def test_revoke_nonexistent_prefix_returns_404() -> None:
    response = client.delete(
        "/admin/api-keys/00000000",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 404


def test_non_admin_key_gets_403() -> None:
    response = client.get(
        "/admin/api-keys",
        headers={"Authorization": f"Bearer {_REGULAR_KEY}"},
    )
    assert response.status_code == 403


def test_no_admin_configured_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEYS", "")
    get_settings.cache_clear()
    _admin_key_hashes.cache_clear()

    response = client.get(
        "/admin/api-keys",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 403


def test_missing_auth_returns_401() -> None:
    response = client.get("/admin/api-keys")
    assert response.status_code == 401


@pytest.mark.parametrize("month", ["2026-8", "1-08", "0000-01"])
def test_monthly_usage_rejects_non_canonical_month(month: str) -> None:
    response = client.get(
        "/admin/usage/monthly",
        params={"key_hash_prefix": hash_api_key(_REGULAR_KEY)[:8], "month": month},
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
