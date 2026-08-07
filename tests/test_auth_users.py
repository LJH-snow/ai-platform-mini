"""Tests for auth API: register, login, me."""

import pytest
from fastapi.testclient import TestClient

from app.api.auth import (
    _clear_auth_service_caches,
    provide_user_service,
    provide_workspace_service,
)
from app.auth.dependencies import provide_api_key_service
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.service import APIKeyService
from app.auth.user_service import UserService
from app.auth.users_repository import InMemoryUserRepository
from app.auth.workspace_service import WorkspaceService
from app.auth.workspaces_repository import (
    InMemoryWorkspaceRepository,
)
from app.main import app

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
