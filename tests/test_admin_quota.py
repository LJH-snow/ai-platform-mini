"""Admin workspace quota API: read/write/clear, validation, auth, 404s."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.auth import (
    _clear_auth_service_caches,
    provide_user_service,
    provide_workspace_service,
)
from app.auth.dependencies import _admin_key_hashes, provide_api_key_service
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKeyRecord
from app.auth.service import APIKeyService
from app.auth.user_service import UserService
from app.auth.users_repository import InMemoryUserRepository
from app.auth.workspace_service import WorkspaceService
from app.auth.workspaces_repository import InMemoryWorkspaceRepository
from app.core.container import provide_quota_service
from app.core.settings import get_settings
from app.main import app
from app.quota.memory_repository import InMemoryQuotaRepository
from app.quota.service import QuotaService
from app.usage.memory_repository import InMemoryUsageRepository

client = TestClient(app)


def _setup() -> tuple[WorkspaceService, QuotaService]:
    user_repo = InMemoryUserRepository()
    ws_repo = InMemoryWorkspaceRepository()
    key_repo = InMemoryAPIKeyRepository([])
    ws_svc = WorkspaceService(workspace_repo=ws_repo, user_repo=user_repo)
    usage_repo = InMemoryUsageRepository()
    quota_svc = QuotaService(
        usage_repository=usage_repo,
        quota_repository=InMemoryQuotaRepository(usage_repo),
        config=__import__("app.quota.models", fromlist=["QuotaConfig"]).QuotaConfig(
            quota_scope="key"
        ),
    )
    _clear_auth_service_caches()
    app.dependency_overrides[provide_user_service] = lambda: UserService(
        repository=user_repo
    )
    app.dependency_overrides[provide_workspace_service] = lambda: ws_svc
    app.dependency_overrides[provide_api_key_service] = lambda: APIKeyService(
        repository=key_repo
    )
    app.dependency_overrides[provide_quota_service] = lambda: quota_svc
    return ws_svc, quota_svc


def _teardown() -> None:
    app.dependency_overrides.clear()
    _clear_auth_service_caches()
    get_settings.cache_clear()
    _admin_key_hashes.cache_clear()


def _register(email: str) -> tuple[str, str]:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "display_name": email.split("@")[0],
            "password": "secret123",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["api_key"], body["workspace"]["id"]


def _setup_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEYS", "sk-admin")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    _admin_key_hashes.cache_clear()
    from app.auth.hash import hash_api_key

    admin = APIKeyRecord(
        key_hash=hash_api_key("sk-admin"), name="admin", status="active"
    )
    key_svc = APIKeyService(repository=InMemoryAPIKeyRepository([admin]))
    app.dependency_overrides[provide_api_key_service] = lambda: key_svc


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_get_quota_returns_inherit_state_for_unset_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup()
    _setup_admin(monkeypatch)
    try:
        _, ws_id = _register("alice@test.com")
        admin = "sk-admin"

        resp = client.get(f"/admin/workspaces/{ws_id}/quota", headers=_auth(admin))
        assert resp.status_code == 200
        body = resp.json()
        assert body["workspace_id"] == ws_id
        assert body["daily_token_limit"] is None
        assert body["monthly_token_limit"] is None
    finally:
        _teardown()


def test_set_and_get_quota_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup()
    _setup_admin(monkeypatch)
    try:
        _, ws_id = _register("alice@test.com")
        admin = "sk-admin"

        put = client.put(
            f"/admin/workspaces/{ws_id}/quota",
            json={"daily_token_limit": 5000, "monthly_token_limit": None},
            headers=_auth(admin),
        )
        assert put.status_code == 200
        assert put.json()["daily_token_limit"] == 5000
        assert put.json()["monthly_token_limit"] is None

        get = client.get(f"/admin/workspaces/{ws_id}/quota", headers=_auth(admin))
        assert get.status_code == 200
        assert get.json()["daily_token_limit"] == 5000
    finally:
        _teardown()


def test_clear_quota_restores_inheritance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup()
    _setup_admin(monkeypatch)
    try:
        _, ws_id = _register("alice@test.com")
        admin = "sk-admin"
        client.put(
            f"/admin/workspaces/{ws_id}/quota",
            json={"daily_token_limit": 5000, "monthly_token_limit": 100000},
            headers=_auth(admin),
        )

        cleared = client.put(
            f"/admin/workspaces/{ws_id}/quota",
            json={"daily_token_limit": None, "monthly_token_limit": None},
            headers=_auth(admin),
        )
        assert cleared.status_code == 200
        assert cleared.json()["daily_token_limit"] is None
        assert cleared.json()["monthly_token_limit"] is None
    finally:
        _teardown()


def test_quota_api_validation_and_404s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup()
    _setup_admin(monkeypatch)
    try:
        _, ws_id = _register("alice@test.com")
        admin = "sk-admin"

        bad = client.put(
            f"/admin/workspaces/{ws_id}/quota",
            json={"daily_token_limit": -5},
            headers=_auth(admin),
        )
        assert bad.status_code == 422

        missing = client.get(
            "/admin/workspaces/00000000-0000-0000-0000-000000000000/quota",
            headers=_auth(admin),
        )
        assert missing.status_code == 404
    finally:
        _teardown()


def test_quota_api_requires_admin_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup()
    _setup_admin(monkeypatch)
    try:
        user_key, ws_id = _register("alice@test.com")

        resp = client.get(f"/admin/workspaces/{ws_id}/quota", headers=_auth(user_key))
        assert resp.status_code in (401, 403)
    finally:
        _teardown()
