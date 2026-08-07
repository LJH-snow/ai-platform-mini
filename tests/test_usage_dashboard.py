"""User-facing Usage Dashboard: workspace aggregation, isolation, legacy compat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from fastapi.testclient import TestClient

from app.api.auth import (
    _clear_auth_service_caches,
    provide_user_service,
    provide_workspace_service,
)
from app.auth.dependencies import provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKeyRecord
from app.auth.service import APIKeyService
from app.auth.user_service import UserService
from app.auth.users_repository import InMemoryUserRepository
from app.auth.workspace_service import WorkspaceService
from app.auth.workspaces_repository import InMemoryWorkspaceRepository
from app.core.container import provide_usage_service
from app.main import app
from app.usage.models import UsageRanking, WorkspaceUsagePoint


@dataclass
class _FakeUsageService:
    """Scripted UsageService stand-in capturing the owner scope."""

    trend: list[WorkspaceUsagePoint] = None  # type: ignore[assignment]
    model_ranking: list[UsageRanking] = None  # type: ignore[assignment]
    key_ranking: list[UsageRanking] = None  # type: ignore[assignment]
    scopes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.trend = []
        self.model_ranking = []
        self.key_ranking = []
        self.scopes = []

    async def get_workspace_trend(
        self, owner_scope: str, days: int
    ) -> list[WorkspaceUsagePoint]:
        del days
        self.scopes.append(owner_scope)
        return self.trend

    async def get_workspace_model_ranking(
        self, owner_scope: str, days: int
    ) -> list[UsageRanking]:
        del days
        self.scopes.append(owner_scope)
        return self.model_ranking

    async def get_workspace_key_ranking(
        self, owner_scope: str, days: int
    ) -> list[UsageRanking]:
        del days
        self.scopes.append(owner_scope)
        return self.key_ranking


def _setup(fake: _FakeUsageService) -> None:
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
    app.dependency_overrides[provide_usage_service] = lambda: fake


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


client = TestClient(app)


def _auth(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def test_dashboard_uses_workspace_scope_and_returns_all_sections() -> None:
    fake = _FakeUsageService()
    _setup(fake)
    try:
        api_key, ws_id = _register("alice@test.com")
        fake.trend = [
            WorkspaceUsagePoint(
                usage_date="2026-08-01", total_tokens=100, request_count=2
            )
        ]
        fake.model_ranking = [
            UsageRanking(name="qwen3:4b", total_tokens=100, request_count=2)
        ]
        fake.key_ranking = [
            UsageRanking(name="abcd1234", total_tokens=100, request_count=2)
        ]

        resp = client.get("/api/v1/usage/dashboard?days=7", headers=_auth(api_key))

        assert resp.status_code == 200
        body = resp.json()
        assert body["trend"][0]["usage_date"] == "2026-08-01"
        assert body["trend"][0]["total_tokens"] == 100
        assert body["model_ranking"][0]["name"] == "qwen3:4b"
        assert body["key_ranking"][0]["name"] == "abcd1234"
        # The scope is the raw workspace id (run-record semantics).
        assert fake.scopes == [ws_id, ws_id, ws_id]
    finally:
        _teardown()


def test_legacy_key_uses_key_hash_scope() -> None:
    fake = _FakeUsageService()
    _setup(fake)
    try:
        legacy_hash = hash_api_key("sk-legacy")
        legacy = APIKeyRecord(key_hash=legacy_hash, name="legacy", status="active")
        key_svc = APIKeyService(repository=InMemoryAPIKeyRepository([legacy]))
        app.dependency_overrides[provide_api_key_service] = lambda: key_svc

        resp = client.get("/api/v1/usage/dashboard", headers=_auth("sk-legacy"))

        assert resp.status_code == 200
        assert fake.scopes == [legacy_hash, legacy_hash, legacy_hash]
    finally:
        _teardown()


def test_days_parameter_is_bounded() -> None:
    fake = _FakeUsageService()
    _setup(fake)
    try:
        api_key, _ = _register("alice@test.com")

        resp = client.get("/api/v1/usage/dashboard?days=999", headers=_auth(api_key))

        assert resp.status_code == 422
    finally:
        _teardown()


async def test_postgres_record_usage_writes_workspace_id() -> None:
    """Lock the Postgres INSERT contract: workspace_id must be persisted.

    Guards against InMemory-vs-Postgres storage drift where the in-memory
    path stores the scope but the SQL path silently drops it (dashboard
    would be empty for workspace users in production).
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.usage.models import UsageRecord
    from app.usage.postgres_repository import PostgresUsageRepository

    class _FakeSession:
        def __init__(self) -> None:
            self.executed: list[tuple[str, dict[str, object]]] = []

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def execute(
            self, statement: object, params: object | None = None
        ) -> None:
            self.executed.append((str(statement), dict(params or {})))  # type: ignore[call-overload]

        async def commit(self) -> None:
            return None

    class _FakeFactory:
        def __init__(self) -> None:
            self.session = _FakeSession()

        def __call__(self) -> _FakeSession:
            return self.session

    factory = _FakeFactory()
    repository = PostgresUsageRepository(
        cast(async_sessionmaker[AsyncSession], factory)
    )
    record = UsageRecord(
        request_id="req-1",
        model="qwen3:4b",
        total_tokens=10,
        api_key_hash="hash-1",
        workspace_id="ws-1",
        usage_date="2026-08-07",
    )

    await repository.record_usage(record)

    sql, params = factory.session.executed[0]
    assert "workspace_id" in sql
    assert params["workspace_id"] == "ws-1"
