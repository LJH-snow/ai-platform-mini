"""Tests for auth API: register, login, me."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.auth import (
    _clear_auth_service_caches,
    provide_user_service,
    provide_workspace_service,
)
from app.auth.dependencies import provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.service import APIKeyService
from app.auth.user_service import UserService
from app.auth.users_repository import InMemoryUserRepository
from app.auth.workspace_service import WorkspaceService
from app.auth.workspaces_repository import (
    InMemoryWorkspaceRepository,
)
from app.core.container import provide_auth_ip_rate_limit_service
from app.main import app
from app.ratelimit.memory import MemorySlidingWindowLimiter
from app.ratelimit.service import RateLimitService

client = TestClient(app)


def _setup_test_services() -> tuple[
    InMemoryUserRepository,
    InMemoryWorkspaceRepository,
    InMemoryAPIKeyRepository,
]:
    """Override auth services with in-memory backends for testing."""
    user_repo = InMemoryUserRepository()
    ws_repo = InMemoryWorkspaceRepository()
    key_repo = InMemoryAPIKeyRepository([])

    user_svc = UserService(repository=user_repo)
    ws_svc = WorkspaceService(workspace_repo=ws_repo, user_repo=user_repo)
    key_svc = APIKeyService(repository=key_repo)

    _clear_auth_service_caches()

    app.dependency_overrides[provide_user_service] = lambda: user_svc
    app.dependency_overrides[provide_workspace_service] = lambda: ws_svc
    app.dependency_overrides[provide_api_key_service] = lambda: key_svc

    return user_repo, ws_repo, key_repo


def _teardown_overrides() -> None:
    app.dependency_overrides.clear()
    _clear_auth_service_caches()


# ── Register ─────────────────────────────────────────────────────────────────


def test_register_creates_user_workspace_and_returns_key() -> None:
    user_repo, ws_repo, key_repo = _setup_test_services()
    try:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "alice@example.com",
                "display_name": "Alice",
                "password": "secret123",
            },
        )
        assert response.status_code == 201
        body = response.json()

        # User created
        assert body["user"]["email"] == "alice@example.com"
        assert body["user"]["display_name"] == "Alice"
        assert body["user"]["status"] == "active"
        assert "id" in body["user"]

        # Default workspace created
        assert body["workspace"]["name"] == "Alice's Workspace"
        assert body["workspace"]["role"] == "owner"

        # API key issued
        assert body["api_key"].startswith("sk-")
        assert len(body["api_key"]) > 40

        # Verify user in repository
        user = user_repo._records.get(body["user"]["id"])
        assert user is not None
        assert user.email == "alice@example.com"

        # Verify workspace
        ws = ws_repo._workspaces.get(body["workspace"]["id"])
        assert ws is not None
        assert ws.created_by_user_id == body["user"]["id"]

        # Verify workspace membership
        member = None
        for m in ws_repo._members:
            if m.user_id == body["user"]["id"]:
                member = m
                break
        assert member is not None
        assert member.role == "owner"
    finally:
        _teardown_overrides()


def test_register_duplicate_email_returns_409() -> None:
    user_repo, ws_repo, key_repo = _setup_test_services()
    try:
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "bob@example.com",
                "display_name": "Bob",
                "password": "secret123",
            },
        )
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "bob@example.com",
                "display_name": "Bob Duplicate",
                "password": "secret456",
            },
        )
        assert response.status_code == 409
    finally:
        _teardown_overrides()


def test_register_invalid_email_returns_422() -> None:
    user_repo, ws_repo, key_repo = _setup_test_services()
    try:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "not-an-email",
                "display_name": "Test",
                "password": "secret123",
            },
        )
        assert response.status_code == 422
    finally:
        _teardown_overrides()


def test_register_short_password_returns_422() -> None:
    user_repo, ws_repo, key_repo = _setup_test_services()
    try:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "test@example.com",
                "display_name": "Test",
                "password": "12345",
            },
        )
        assert response.status_code == 422
    finally:
        _teardown_overrides()


def test_register_ip_rate_limit_returns_429_after_allowed_requests() -> None:
    _ = _setup_test_services()
    try:
        auth_ip_service = RateLimitService(
            limiter=MemorySlidingWindowLimiter(limit=2, window_seconds=60)
        )
        app.dependency_overrides[provide_auth_ip_rate_limit_service] = lambda: (
            auth_ip_service
        )

        first = client.post(
            "/api/v1/auth/register",
            json={
                "email": "limit-one@example.com",
                "display_name": "Limit One",
                "password": "secret123",
            },
        )
        assert first.status_code == 201
        assert first.headers["X-RateLimit-Limit"] == "2"
        assert first.headers["X-RateLimit-Remaining"] == "1"

        second = client.post(
            "/api/v1/auth/register",
            json={
                "email": "limit-two@example.com",
                "display_name": "Limit Two",
                "password": "secret123",
            },
        )
        assert second.status_code == 201
        assert second.headers["X-RateLimit-Remaining"] == "0"

        third = client.post(
            "/api/v1/auth/register",
            json={
                "email": "limit-three@example.com",
                "display_name": "Limit Three",
                "password": "secret123",
            },
        )
        assert third.status_code == 429
        assert int(third.headers["Retry-After"]) > 0
    finally:
        _teardown_overrides()


def test_register_ip_rate_limit_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = _setup_test_services()
    try:
        monkeypatch.setattr(
            "app.ratelimit.dependencies.get_settings",
            lambda: SimpleNamespace(auth_ip_rate_limit_enabled=False),
        )
        auth_ip_service = RateLimitService(
            limiter=MemorySlidingWindowLimiter(limit=1, window_seconds=60)
        )
        app.dependency_overrides[provide_auth_ip_rate_limit_service] = lambda: (
            auth_ip_service
        )

        first = client.post(
            "/api/v1/auth/register",
            json={
                "email": "no-limit-one@example.com",
                "display_name": "No Limit One",
                "password": "secret123",
            },
        )
        assert first.status_code == 201

        second = client.post(
            "/api/v1/auth/register",
            json={
                "email": "no-limit-two@example.com",
                "display_name": "No Limit Two",
                "password": "secret123",
            },
        )
        assert second.status_code == 201
    finally:
        _teardown_overrides()


# ── Login ───────────────────────────────────────────────────────────────────


def test_login_in_memory_mode_returns_503() -> None:
    """Login requires postgres storage; memory mode returns 503."""
    user_repo, ws_repo, key_repo = _setup_test_services()
    try:
        # Pre-create user
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "carol@example.com",
                "display_name": "Carol",
                "password": "secret123",
            },
        )
        assert response.status_code == 201

        # Login should fail in memory mode
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "carol@example.com", "password": "secret123"},
        )
        assert response.status_code == 503
    finally:
        _teardown_overrides()


def test_login_wrong_password_returns_401() -> None:
    """Even with the correct memory-mode 503 bypass, wrong password fails."""
    user_repo, ws_repo, key_repo = _setup_test_services()
    try:
        # Pre-create user
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "dave@example.com",
                "display_name": "Dave",
                "password": "secret123",
            },
        )

        # Direct service-level test since HTTP returns 503
        user_svc = UserService(repository=user_repo)

        from app.exceptions.base import AuthenticationError

        with pytest.raises(AuthenticationError, match="Invalid email or password"):
            # Run async code via asyncio
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    user_svc.login("dave@example.com", "wrong-password")
                )
            finally:
                loop.close()
    finally:
        _teardown_overrides()


# ── Me ──────────────────────────────────────────────────────────────────────


def test_me_without_identity_returns_401() -> None:
    user_repo, ws_repo, key_repo = _setup_test_services()
    try:
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401
    finally:
        _teardown_overrides()


def test_me_with_valid_key_returns_user_and_workspaces() -> None:
    """Register a user then call /me with the issued API key."""
    user_repo, ws_repo, key_repo = _setup_test_services()
    try:
        # Register to get a valid key
        reg_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "eve@example.com",
                "display_name": "Eve",
                "password": "secret123",
            },
        )
        assert reg_response.status_code == 201
        api_key = reg_response.json()["api_key"]

        # Call /me with the key
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["user"]["email"] == "eve@example.com"
        assert body["user"]["display_name"] == "Eve"
        assert len(body["workspaces"]) >= 1
        assert body["workspaces"][0]["role"] == "owner"
    finally:
        _teardown_overrides()


# ── Logout ─────────────────────────────────────────────────────────────────


def test_logout_revokes_current_user_key() -> None:
    user_repo, ws_repo, key_repo = _setup_test_services()
    try:
        reg_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "logout@example.com",
                "display_name": "Logout",
                "password": "secret123",
            },
        )
        assert reg_response.status_code == 201
        api_key = reg_response.json()["api_key"]
        headers = {"Authorization": f"Bearer {api_key}"}

        me_before = client.get("/api/v1/auth/me", headers=headers)
        assert me_before.status_code == 200

        logout_response = client.post("/api/v1/auth/logout", headers=headers)
        assert logout_response.status_code == 200
        body = logout_response.json()
        assert body["revoked"] is True
        assert len(body["key_hash_prefix"]) == 8

        me_after = client.get("/api/v1/auth/me", headers=headers)
        assert me_after.status_code == 401
    finally:
        _teardown_overrides()


def test_logout_without_auth_returns_401() -> None:
    _ = _setup_test_services()
    try:
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 401
    finally:
        _teardown_overrides()


# ── User API Key management ────────────────────────────────────────────────


def test_user_can_list_and_create_own_api_keys() -> None:
    user_repo, ws_repo, key_repo = _setup_test_services()
    try:
        reg_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "keyowner@example.com",
                "display_name": "Key Owner",
                "password": "secret123",
            },
        )
        assert reg_response.status_code == 201
        api_key = reg_response.json()["api_key"]
        headers = {"Authorization": f"Bearer {api_key}"}

        list_before = client.get("/api/v1/auth/keys", headers=headers)
        assert list_before.status_code == 200
        assert len(list_before.json()) == 1
        assert list_before.json()[0]["status"] == "active"

        create_response = client.post(
            "/api/v1/auth/keys",
            headers=headers,
            json={"name": "ci-dev"},
        )
        assert create_response.status_code == 201
        body = create_response.json()
        assert body["raw_key"].startswith("sk-")
        assert len(body["key_hash_prefix"]) == 8

        list_after = client.get("/api/v1/auth/keys", headers=headers)
        assert list_after.status_code == 200
        assert len(list_after.json()) == 2
        assert {item["name"] for item in list_after.json()} >= {
            "keyowner@example.com-default",
            "ci-dev",
        }

        me_with_new_key = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {body['raw_key']}"},
        )
        assert me_with_new_key.status_code == 200
    finally:
        _teardown_overrides()


def test_user_can_revoke_own_api_key_by_prefix() -> None:
    user_repo, ws_repo, key_repo = _setup_test_services()
    try:
        reg_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "revoker@example.com",
                "display_name": "Revoker",
                "password": "secret123",
            },
        )
        assert reg_response.status_code == 201
        api_key = reg_response.json()["api_key"]
        headers = {"Authorization": f"Bearer {api_key}"}

        create_response = client.post(
            "/api/v1/auth/keys",
            headers=headers,
            json={"name": "expire-me"},
        )
        assert create_response.status_code == 201
        created = create_response.json()

        revoke_response = client.delete(
            f"/api/v1/auth/keys/{created['key_hash_prefix']}",
            headers=headers,
        )
        assert revoke_response.status_code == 200
        assert revoke_response.json()["revoked"] is True

        me_with_revoked_key = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {created['raw_key']}"},
        )
        assert me_with_revoked_key.status_code == 401
    finally:
        _teardown_overrides()


def test_user_cannot_revoke_another_users_key() -> None:
    user_repo, ws_repo, key_repo = _setup_test_services()
    try:
        user_a = client.post(
            "/api/v1/auth/register",
            json={
                "email": "owner-a@example.com",
                "display_name": "Owner A",
                "password": "secret123",
            },
        )
        assert user_a.status_code == 201
        key_a = user_a.json()["api_key"]
        headers_a = {"Authorization": f"Bearer {key_a}"}

        user_b = client.post(
            "/api/v1/auth/register",
            json={
                "email": "owner-b@example.com",
                "display_name": "Owner B",
                "password": "secret123",
            },
        )
        assert user_b.status_code == 201
        key_b = user_b.json()["api_key"]
        prefix_b = hash_api_key(key_b)[:8]

        revoke_b = client.delete(
            f"/api/v1/auth/keys/{prefix_b}",
            headers=headers_a,
        )
        assert revoke_b.status_code == 404

        me_b = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {key_b}"},
        )
        assert me_b.status_code == 200
    finally:
        _teardown_overrides()


def test_user_key_endpoints_require_auth() -> None:
    _ = _setup_test_services()
    try:
        response = client.get("/api/v1/auth/keys")
        assert response.status_code == 401
    finally:
        _teardown_overrides()
