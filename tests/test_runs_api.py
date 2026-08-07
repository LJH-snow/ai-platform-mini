"""User-facing Agent Run replay endpoints: tenant scoping, legacy compat, 404."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.agents.models import RunStatus, StopReason
from app.api.auth import (
    _clear_auth_service_caches,
    provide_user_service,
    provide_workspace_service,
)
from app.auth.dependencies import provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKey, APIKeyRecord
from app.auth.service import APIKeyService
from app.auth.user_service import UserService
from app.auth.users_repository import InMemoryUserRepository
from app.auth.workspace_service import WorkspaceService
from app.auth.workspaces_repository import InMemoryWorkspaceRepository
from app.core.container import provide_agent_run_record_service
from app.core.context import RequestContext
from app.main import app
from app.schemas.agent import AgentRunRequest, AgentRunResponse, AgentUsage

client = TestClient(app)


@dataclass
class _FakeRecordService:
    """In-memory stand-in for AgentRunRecordService with scope filtering."""

    records: list[dict[str, object]] = field(default_factory=list)

    async def save(
        self,
        response: AgentRunResponse,
        request: AgentRunRequest,
        context: RequestContext,
        api_key: APIKey,
        model: str | None = None,
        *,
        agent_id: str | None = None,
        prompt_ref: str | None = None,
    ) -> None:
        identity = context.identity
        workspace_id = identity.workspace_id if identity else None
        self.records.append(
            {
                "run_id": response.run_id,
                "api_key_hash": api_key.key,
                "workspace_id": workspace_id,
                "status": response.status.value,
            }
        )

    def _to_row(self, record: dict[str, object]) -> object:
        from types import SimpleNamespace

        payload: dict[str, object] = {
            "run_id": record["run_id"],
            "status": record["status"],
            "steps": [],
        }
        return SimpleNamespace(
            run_id=record["run_id"],
            request_id="req-1",
            api_key_hash=record["api_key_hash"],
            api_key_name="test",
            model="m",
            status=record["status"],
            stop_reason="direct_answer",
            started_at=None,
            completed_at=None,
            duration_ms=None,
            total_tokens=None,
            payload=payload,
        )

    async def list_runs(
        self,
        limit: int = 50,
        status: str | None = None,
        *,
        owner_scope: str | None = None,
        agent_id: str | None = None,
    ) -> list[object]:
        del status, agent_id
        rows = [row for row in self.records]
        if owner_scope is not None:
            rows = [
                row
                for row in rows
                if row["workspace_id"] == owner_scope
                or (row["workspace_id"] is None and row["api_key_hash"] == owner_scope)
            ]
        return [self._to_row(row) for row in rows[:limit]]

    async def get_run(
        self, run_id: str, *, owner_scope: str | None = None
    ) -> object | None:
        for row in self.records:
            if row["run_id"] != run_id:
                continue
            if owner_scope is not None:
                if row["workspace_id"] == owner_scope:
                    return self._to_row(row)
                if row["workspace_id"] is None and row["api_key_hash"] == owner_scope:
                    return self._to_row(row)
                return None
            return self._to_row(row)
        return None


def _setup(record_service: _FakeRecordService) -> None:
    user_repo = InMemoryUserRepository()
    ws_repo = InMemoryWorkspaceRepository()
    key_repo = InMemoryAPIKeyRepository([])
    _clear_auth_service_caches()
    app.dependency_overrides[provide_user_service] = lambda: UserService(
        repository=user_repo
    )
    app.dependency_overrides[provide_workspace_service] = lambda: WorkspaceService(
        workspace_repo=ws_repo, user_repo=user_repo
    )
    app.dependency_overrides[provide_api_key_service] = lambda: APIKeyService(
        repository=key_repo
    )
    app.dependency_overrides[provide_agent_run_record_service] = lambda: record_service


def _teardown() -> None:
    app.dependency_overrides.clear()
    _clear_auth_service_caches()


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


def _auth(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _save_run(
    record_service: _FakeRecordService,
    run_id: str,
    api_key: APIKey,
    workspace_id: str | None,
) -> None:
    import asyncio

    from app.auth.identity import IdentityContext

    context = RequestContext(
        request_id="req-1",
        identity=IdentityContext(
            user_id="u1",
            workspace_id=workspace_id,
            api_key_id=None,
            api_key_hash=api_key.key,
            role="admin",
        ),
    )
    response = AgentRunResponse(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        stop_reason=StopReason.DIRECT_ANSWER,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        duration_ms=1.0,
        usage=AgentUsage(
            prompt_tokens=1, completion_tokens=1, total_tokens=2, estimated=False
        ),
    )
    asyncio.run(
        record_service.save(
            response,
            AgentRunRequest(message="hi"),
            context,
            api_key,
        )
    )


def test_user_lists_and_reads_own_workspace_runs() -> None:
    record_service = _FakeRecordService()
    _setup(record_service)
    try:
        key_a, ws_a = _register("alice@test.com")
        _save_run(
            record_service,
            "run-ws-a",
            APIKey(key="key-a", name="a"),
            ws_a,
        )

        list_resp = client.get("/api/v1/runs", headers=_auth(key_a))
        assert list_resp.status_code == 200
        run_ids = [run["run_id"] for run in list_resp.json()]
        assert run_ids == ["run-ws-a"]

        detail_resp = client.get("/api/v1/runs/run-ws-a", headers=_auth(key_a))
        assert detail_resp.status_code == 200
        assert detail_resp.json()["run_id"] == "run-ws-a"
    finally:
        _teardown()


def test_cross_workspace_run_is_404() -> None:
    record_service = _FakeRecordService()
    _setup(record_service)
    try:
        key_a, ws_a = _register("alice@test.com")
        key_b, _ = _register("bob@test.com")
        _save_run(
            record_service,
            "run-alice",
            APIKey(key="key-a", name="a"),
            ws_a,
        )

        # Bob cannot see Alice's run in the list…
        list_resp = client.get("/api/v1/runs", headers=_auth(key_b))
        assert list_resp.status_code == 200
        assert list_resp.json() == []

        # …nor fetch it (uniform 404).
        detail_resp = client.get("/api/v1/runs/run-alice", headers=_auth(key_b))
        assert detail_resp.status_code == 404
    finally:
        _teardown()


def test_legacy_key_sees_only_own_legacy_runs() -> None:
    record_service = _FakeRecordService()
    _setup(record_service)
    try:
        # Legacy (unbound) key: workspace_id NULL rows match by key hash.
        legacy_hash = hash_api_key("sk-legacy")
        legacy = APIKeyRecord(key_hash=legacy_hash, name="legacy", status="active")
        key_svc = APIKeyService(repository=InMemoryAPIKeyRepository([legacy]))
        app.dependency_overrides[provide_api_key_service] = lambda: key_svc

        _save_run(
            record_service,
            "run-legacy",
            APIKey(key=legacy_hash, name="legacy"),
            None,
        )
        # A workspace-bound run must not leak to the legacy key.
        key_a, ws_a = _register("alice@test.com")
        _save_run(record_service, "run-ws", APIKey(key="key-a", name="a"), ws_a)

        list_resp = client.get("/api/v1/runs", headers=_auth("sk-legacy"))
        assert list_resp.status_code == 200
        assert [run["run_id"] for run in list_resp.json()] == ["run-legacy"]

        detail_resp = client.get("/api/v1/runs/run-ws", headers=_auth("sk-legacy"))
        assert detail_resp.status_code == 404
    finally:
        _teardown()


def test_unknown_run_is_404() -> None:
    record_service = _FakeRecordService()
    _setup(record_service)
    try:
        key_a, _ = _register("alice@test.com")
        resp = client.get("/api/v1/runs/does-not-exist", headers=_auth(key_a))
        assert resp.status_code == 404
    finally:
        _teardown()
