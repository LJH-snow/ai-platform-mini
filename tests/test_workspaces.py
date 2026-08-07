"""Tests for workspaces API: CRUD + member management."""

import asyncio

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
from app.auth.users_repository import InMemoryUserRepository, UserRecord
from app.auth.workspace_service import WorkspaceService
from app.auth.workspaces_repository import (
    InMemoryWorkspaceRepository,
)
from app.main import app

client = TestClient(app)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _register_and_get_key(
    user_repo: InMemoryUserRepository,
    ws_repo: InMemoryWorkspaceRepository,
    key_repo: InMemoryAPIKeyRepository,
    email: str,
    display_name: str,
    password: str = "secret123",
) -> dict[str, object]:
    """Register a user and return the full response body."""
    _clear_auth_service_caches()

    user_svc = UserService(repository=user_repo)
    ws_svc = WorkspaceService(workspace_repo=ws_repo, user_repo=user_repo)
    key_svc = APIKeyService(repository=key_repo)

    app.dependency_overrides[provide_user_service] = lambda: user_svc
    app.dependency_overrides[provide_workspace_service] = lambda: ws_svc
    app.dependency_overrides[provide_api_key_service] = lambda: key_svc

    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "display_name": display_name,
            "password": password,
        },
    )
    assert response.status_code == 201, f"Register failed: {response.json()}"
    result: dict[str, object] = response.json()
    return result


def _auth_header(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


# ── Fixture ─────────────────────────────────────────────────────────────────


@pytest.fixture
def setup() -> tuple[
    InMemoryUserRepository, InMemoryWorkspaceRepository, InMemoryAPIKeyRepository
]:
    """Set up test services with in-memory backends."""
    user_repo = InMemoryUserRepository()
    ws_repo = InMemoryWorkspaceRepository()
    key_repo = InMemoryAPIKeyRepository([])
    return user_repo, ws_repo, key_repo


def _teardown() -> None:
    app.dependency_overrides.clear()
    _clear_auth_service_caches()


# ── Create Workspace ────────────────────────────────────────────────────────


def test_create_workspace(setup: tuple) -> None:
    user_repo, ws_repo, key_repo = setup
    try:
        reg = _register_and_get_key(
            user_repo, ws_repo, key_repo, "alice@example.com", "Alice"
        )
        api_key = str(reg["api_key"])

        response = client.post(
            "/api/v1/workspaces",
            json={"name": "Team Alpha"},
            headers=_auth_header(api_key),
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Team Alpha"
        assert body["role"] == "owner"
        assert "id" in body
    finally:
        _teardown()


def test_create_workspace_empty_name_returns_422(setup: tuple) -> None:
    user_repo, ws_repo, key_repo = setup
    try:
        reg = _register_and_get_key(
            user_repo, ws_repo, key_repo, "bob@example.com", "Bob"
        )
        api_key = str(reg["api_key"])

        response = client.post(
            "/api/v1/workspaces",
            json={"name": ""},
            headers=_auth_header(api_key),
        )
        assert response.status_code == 422
    finally:
        _teardown()


def test_create_workspace_without_auth_returns_401(setup: tuple) -> None:
    user_repo, ws_repo, key_repo = setup
    try:
        response = client.post("/api/v1/workspaces", json={"name": "No Auth"})
        assert response.status_code == 401
    finally:
        _teardown()


# ── List Workspaces ─────────────────────────────────────────────────────────


def test_list_workspaces(setup: tuple) -> None:
    user_repo, ws_repo, key_repo = setup
    try:
        reg = _register_and_get_key(
            user_repo, ws_repo, key_repo, "carol@example.com", "Carol"
        )
        api_key = str(reg["api_key"])

        # Create a second workspace
        client.post(
            "/api/v1/workspaces",
            json={"name": "Team Beta"},
            headers=_auth_header(api_key),
        )

        response = client.get("/api/v1/workspaces", headers=_auth_header(api_key))
        assert response.status_code == 200
        workspaces = response.json()
        assert isinstance(workspaces, list)
        assert len(workspaces) >= 2
        # Each workspace should have name, role, member_count
        for ws in workspaces:
            assert "id" in ws
            assert "name" in ws
            assert "role" in ws
            assert "member_count" in ws
    finally:
        _teardown()


# ── Member Management ──────────────────────────────────────────────────────


def test_add_member_to_workspace(setup: tuple) -> None:
    user_repo, ws_repo, key_repo = setup
    try:
        # Register owner
        reg = _register_and_get_key(
            user_repo, ws_repo, key_repo, "owner@example.com", "Owner"
        )
        owner_key = str(reg["api_key"])
        workspace_id = str(reg["workspace"]["id"])  # type: ignore[index]

        # Register target member via service directly (to avoid creating
        # another workspace)
        target_id = "target-uuid"
        user_repo._records[target_id] = UserRecord(
            id=target_id,
            email="member@example.com",
            display_name="Member",
            password_salt="salt",
            password_hash="hash",
            status="active",
        )
        user_repo._by_email["member@example.com"] = target_id

        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/members",
            json={"email": "member@example.com", "role": "member"},
            headers=_auth_header(owner_key),
        )
        assert response.status_code == 201
        body = response.json()
        assert body["role"] == "member"
        assert body["user_id"] == target_id
    finally:
        _teardown()


def test_add_member_requires_owner_or_admin(setup: tuple) -> None:
    user_repo, ws_repo, key_repo = setup
    try:
        # Register owner and workspace
        reg = _register_and_get_key(
            user_repo, ws_repo, key_repo, "owner2@example.com", "Owner2"
        )
        workspace_id = str(reg["workspace"]["id"])  # type: ignore[index]

        # Register second user with member role
        reg2 = _register_and_get_key(
            user_repo, ws_repo, key_repo, "viewer@example.com", "Viewer"
        )
        viewer_key = str(reg2["api_key"])

        # Add viewer as member to the workspace
        ws_svc = WorkspaceService(workspace_repo=ws_repo, user_repo=user_repo)

        awaitable = ws_svc.add_member(
            workspace_id=workspace_id,
            actor_user_id=reg["user"]["id"],  # type: ignore[index]
            target_email="viewer@example.com",
            role="viewer",
        )
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(awaitable)
        finally:
            loop.close()

        # Now viewer tries to add a member — should fail
        # But first register a third user
        user_id_3 = "third-uuid"
        user_repo._records[user_id_3] = UserRecord(
            id=user_id_3,
            email="third@example.com",
            display_name="Third",
            password_salt="salt",
            password_hash="hash",
            status="active",
        )
        user_repo._by_email["third@example.com"] = user_id_3

        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/members",
            json={"email": "third@example.com", "role": "member"},
            headers=_auth_header(viewer_key),
        )
        assert response.status_code == 403
    finally:
        _teardown()


def test_list_members(setup: tuple) -> None:
    user_repo, ws_repo, key_repo = setup
    try:
        reg = _register_and_get_key(
            user_repo, ws_repo, key_repo, "admin@example.com", "Admin"
        )
        admin_key = str(reg["api_key"])
        workspace_id = str(reg["workspace"]["id"])  # type: ignore[index]

        response = client.get(
            f"/api/v1/workspaces/{workspace_id}/members",
            headers=_auth_header(admin_key),
        )
        assert response.status_code == 200
        members = response.json()
        assert isinstance(members, list)
        assert len(members) >= 1  # at least the owner
        # Owner should be in the list
        owner_emails = [m["email"] for m in members if m["role"] == "owner"]
        assert len(owner_emails) >= 1
    finally:
        _teardown()


def test_remove_member(setup: tuple) -> None:
    user_repo, ws_repo, key_repo = setup
    try:
        reg = _register_and_get_key(
            user_repo, ws_repo, key_repo, "boss@example.com", "Boss"
        )
        owner_key = str(reg["api_key"])
        workspace_id = str(reg["workspace"]["id"])  # type: ignore[index]

        # Add a member
        member_id = "member-to-remove"
        user_repo._records[member_id] = UserRecord(
            id=member_id,
            email="temp@example.com",
            display_name="Temp",
            password_salt="salt",
            password_hash="hash",
            status="active",
        )
        user_repo._by_email["temp@example.com"] = member_id

        client.post(
            f"/api/v1/workspaces/{workspace_id}/members",
            json={"email": "temp@example.com", "role": "member"},
            headers=_auth_header(owner_key),
        )

        # Remove the member
        response = client.delete(
            f"/api/v1/workspaces/{workspace_id}/members/{member_id}",
            headers=_auth_header(owner_key),
        )
        assert response.status_code == 200

        # Verify removal
        response2 = client.get(
            f"/api/v1/workspaces/{workspace_id}/members",
            headers=_auth_header(owner_key),
        )
        members = response2.json()
        user_ids = [m["user_id"] for m in members]
        assert member_id not in user_ids
    finally:
        _teardown()


def test_cannot_remove_owner(setup: tuple) -> None:
    user_repo, ws_repo, key_repo = setup
    try:
        reg = _register_and_get_key(
            user_repo, ws_repo, key_repo, "king@example.com", "King"
        )
        owner_key = str(reg["api_key"])
        workspace_id = str(reg["workspace"]["id"])  # type: ignore[index]
        owner_user_id = str(reg["user"]["id"])  # type: ignore[index]

        response = client.delete(
            f"/api/v1/workspaces/{workspace_id}/members/{owner_user_id}",
            headers=_auth_header(owner_key),
        )
        assert response.status_code == 403
    finally:
        _teardown()


def test_update_member_role(setup: tuple) -> None:
    user_repo, ws_repo, key_repo = setup
    try:
        reg = _register_and_get_key(
            user_repo, ws_repo, key_repo, "chief@example.com", "Chief"
        )
        owner_key = str(reg["api_key"])
        workspace_id = str(reg["workspace"]["id"])  # type: ignore[index]

        # Add a member
        member_id = "member-to-promote"
        user_repo._records[member_id] = UserRecord(
            id=member_id,
            email="promote@example.com",
            display_name="Promotee",
            password_salt="salt",
            password_hash="hash",
            status="active",
        )
        user_repo._by_email["promote@example.com"] = member_id

        client.post(
            f"/api/v1/workspaces/{workspace_id}/members",
            json={"email": "promote@example.com", "role": "member"},
            headers=_auth_header(owner_key),
        )

        # Promote to admin
        response = client.put(
            f"/api/v1/workspaces/{workspace_id}/members/{member_id}",
            json={"role": "admin"},
            headers=_auth_header(owner_key),
        )
        assert response.status_code == 200
        assert response.json()["role"] == "admin"
    finally:
        _teardown()
