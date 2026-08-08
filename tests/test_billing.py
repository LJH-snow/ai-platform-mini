"""Sprint E1a Billing tests: seed idempotency, quota inheritance chain
(override > plan > default), entitlement feature/limit checks, resource
ceiling checkpoints, and billing/subscription admin APIs."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api import rag as rag_api
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
from app.billing.entitlement import EntitlementService
from app.billing.memory_repository import InMemoryBillingRepository
from app.billing.models import Plan, Subscription, SubscriptionStatus
from app.billing.seeds import FREE_PLAN_ID, PRO_PLAN_ID, build_seed_plans
from app.billing.service import PlanService
from app.core.container import (
    provide_billing_repository,
    provide_entitlement_service,
    provide_plan_service,
    provide_quota_service,
    provide_rag_ingestion_service,
    provide_usage_service,
)
from app.core.settings import get_settings
from app.exceptions.base import QuotaExceededError, ValidationError
from app.main import app
from app.quota.memory_repository import InMemoryQuotaRepository
from app.quota.models import QuotaConfig
from app.quota.service import QuotaService
from app.rag.queue import IngestionTask, IngestionTaskStatus
from app.rag.vector_store import DocumentSummary
from app.usage.memory_repository import InMemoryUsageRepository
from app.usage.models import UsageRecord

client = TestClient(app)


# ── Fixtures / helpers ──────────────────────────────────────────────────────


def _billing_repo_with_seeds() -> InMemoryBillingRepository:
    repo = InMemoryBillingRepository()
    for plan in build_seed_plans():
        repo._plans_by_id[plan.id] = plan
        repo._plans_by_name[plan.name] = plan
    return repo


def _quota_service(
    billing_repo: InMemoryBillingRepository,
    *,
    scope: str = "workspace",
    monthly: int | None = 1000,
) -> QuotaService:
    usage_repo = InMemoryUsageRepository()
    return QuotaService(
        usage_repository=usage_repo,
        quota_repository=InMemoryQuotaRepository(usage_repo),
        config=QuotaConfig(monthly_token_limit=monthly, quota_scope=scope),  # type: ignore[arg-type]
        billing_repository=billing_repo,
    )


def _setup() -> tuple[WorkspaceService, InMemoryBillingRepository]:
    """Overrides for API tests: shared user/workspace/billing stores."""
    user_repo = InMemoryUserRepository()
    ws_repo = InMemoryWorkspaceRepository()
    key_repo = InMemoryAPIKeyRepository([])
    billing_repo = _billing_repo_with_seeds()
    entitlement = EntitlementService(billing_repo)
    ws_svc = WorkspaceService(
        workspace_repo=ws_repo, user_repo=user_repo, entitlement=entitlement
    )
    usage_repo = InMemoryUsageRepository()
    quota_svc = QuotaService(
        usage_repository=usage_repo,
        quota_repository=InMemoryQuotaRepository(usage_repo),
        config=QuotaConfig(monthly_token_limit=1000, quota_scope="workspace"),
        billing_repository=billing_repo,
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
    app.dependency_overrides[provide_billing_repository] = lambda: billing_repo
    app.dependency_overrides[provide_plan_service] = lambda: PlanService(billing_repo)
    app.dependency_overrides[provide_entitlement_service] = lambda: entitlement
    app.dependency_overrides[provide_usage_service] = lambda: _UsageServiceProxy(
        usage_repo
    )
    return ws_svc, billing_repo


class _UsageServiceProxy:
    """Minimal UsageService-shaped wrapper over the shared usage repo."""

    def __init__(self, usage_repo: InMemoryUsageRepository) -> None:
        from app.usage.service import UsageService

        self._inner = UsageService(repository=usage_repo)
        self._repository = usage_repo

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def _teardown() -> None:
    app.dependency_overrides.clear()
    _clear_auth_service_caches()
    get_settings.cache_clear()
    _admin_key_hashes.cache_clear()


def _setup_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEYS", "sk-admin")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    # Replace the key store with one that also carries the admin key;
    # later register() calls add user keys into the same store.
    from app.auth.hash import hash_api_key

    admin_key_service = APIKeyService(
        repository=InMemoryAPIKeyRepository(
            [
                APIKeyRecord(
                    key_hash=hash_api_key("sk-admin"), name="admin", status="active"
                )
            ]
        )
    )
    app.dependency_overrides[provide_api_key_service] = lambda: admin_key_service


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


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


def _subscribe(
    admin: str, workspace_id: str, plan_id: str, status: str = "ACTIVE"
) -> None:
    resp = client.post(
        f"/admin/workspaces/{workspace_id}/subscription",
        json={"plan_id": plan_id, "status": status},
        headers=_auth(admin),
    )
    assert resp.status_code == 200, resp.text


# ── Seed idempotency ────────────────────────────────────────────────────────


def test_seed_plans_idempotent() -> None:
    repo = InMemoryBillingRepository()
    for _ in range(2):
        for plan in build_seed_plans():
            repo._plans_by_id[plan.id] = plan
            repo._plans_by_name[plan.name] = plan

    names = sorted(plan.name for plan in repo._plans_by_id.values())
    assert names == ["enterprise", "free", "pro"]
    # Second seed pass must not duplicate any plan.
    assert len(repo._plans_by_id) == 3
    assert len(repo._plans_by_name) == 3


# ── Quota inheritance chain ────────────────────────────────────────────────


async def test_no_subscription_uses_settings_in_workspace_mode() -> None:
    repo = InMemoryBillingRepository()
    service = _quota_service(repo, monthly=1000)

    # No subscription → settings default applies.
    with pytest.raises(QuotaExceededError):
        await service.reserve("key-a", max_tokens=1001, workspace_id="ws-1")
    assert (
        await service.reserve("key-a", max_tokens=100, workspace_id="ws-1") is not None
    )


async def test_plan_overrides_settings_until_override_wins() -> None:
    repo = _billing_repo_with_seeds()
    service = _quota_service(repo, monthly=1000)
    free_plan = repo._plans_by_name["free"]  # monthly 100k

    await repo.create_subscription(
        _sub(repo, "ws-1", free_plan.id, SubscriptionStatus.ACTIVE)
    )
    # Plan (100k) replaces settings (1000): 10k passes.
    assert (
        await service.reserve("key-a", max_tokens=10_000, workspace_id="ws-1")
        is not None
    )
    # Plan ceiling itself is enforced.
    with pytest.raises(QuotaExceededError):
        await service.reserve("key-a", max_tokens=100_001, workspace_id="ws-1")

    # Workspace override sits ABOVE the plan: 50k passes (plan says 100k
    # limit, override 200k wins).
    await service._quota_repo.set_workspace_quota(  # type: ignore[attr-defined]
        "ws-1", daily=None, monthly=200_000
    )
    assert (
        await service.reserve("key-a", max_tokens=150_000, workspace_id="ws-1")
        is not None
    )


async def test_expired_subscription_skips_plan_layer() -> None:
    repo = _billing_repo_with_seeds()
    service = _quota_service(repo, monthly=1000)
    free_plan = repo._plans_by_name["free"]

    await repo.create_subscription(
        _sub(repo, "ws-1", free_plan.id, SubscriptionStatus.EXPIRED)
    )
    # EXPIRED does not participate: settings (1000) applies again.
    with pytest.raises(QuotaExceededError):
        await service.reserve("key-a", max_tokens=1001, workspace_id="ws-1")


async def test_key_scope_never_consults_plan() -> None:
    """Explicit branch: plan is workspace-dimension and must not leak."""
    repo = _billing_repo_with_seeds()
    service = _quota_service(repo, scope="key", monthly=1000)
    pro_plan = repo._plans_by_name["pro"]  # monthly 10M

    await repo.create_subscription(
        _sub(repo, "ws-1", pro_plan.id, SubscriptionStatus.ACTIVE)
    )
    # Key scope + workspace id supplied: settings (1000) still applies.
    with pytest.raises(QuotaExceededError):
        await service.reserve("key-a", max_tokens=1001, workspace_id="ws-1")


def _sub(
    repo: InMemoryBillingRepository, workspace_id: str, plan_id: str, status: object
) -> Subscription:
    from app.billing.models import Subscription

    return Subscription(
        id=f"sub-{workspace_id}",
        workspace_id=workspace_id,
        plan_id=plan_id,
        status=status,  # type: ignore[arg-type]
    )


# ── Entitlement: feature / limit / legacy semantics ────────────────────────


async def test_entitlement_legacy_fully_open() -> None:
    entitlement = EntitlementService(InMemoryBillingRepository())
    assert await entitlement.check_feature("ws-1", "reranker") is True
    assert await entitlement.check_limit("ws-1", "agent", 10_000) is True
    # require_limit is a no-op without a subscription.
    await entitlement.require_limit("ws-1", "member", 10_000)


async def test_entitlement_follows_plan_values() -> None:
    repo = _billing_repo_with_seeds()
    entitlement = EntitlementService(repo)
    free_plan = repo._plans_by_name["free"]  # 3 agents / 5 docs / no reranker
    await repo.create_subscription(
        _sub(repo, "ws-1", free_plan.id, SubscriptionStatus.ACTIVE)
    )

    assert await entitlement.check_feature("ws-1", "reranker") is False
    assert await entitlement.check_feature("ws-1", "benchmark") is False
    assert await entitlement.check_limit("ws-1", "agent", 2) is True
    assert await entitlement.check_limit("ws-1", "agent", 3) is False
    assert await entitlement.check_limit("ws-1", "document", 5) is False
    assert await entitlement.check_limit("ws-1", "member", 0) is True  # no ceiling


async def test_entitlement_require_limit_message() -> None:
    repo = _billing_repo_with_seeds()
    entitlement = EntitlementService(repo)
    free_plan = repo._plans_by_name["free"]
    await repo.create_subscription(
        _sub(repo, "ws-1", free_plan.id, SubscriptionStatus.ACTIVE)
    )

    with pytest.raises(ValidationError) as excinfo:
        await entitlement.require_limit("ws-1", "agent", 3)
    assert "已达 free 计划上限（agent 3）。" in str(excinfo.value)


async def test_entitlement_expired_subscription_is_legacy() -> None:
    repo = _billing_repo_with_seeds()
    entitlement = EntitlementService(repo)
    free_plan = repo._plans_by_name["free"]
    await repo.create_subscription(
        _sub(repo, "ws-1", free_plan.id, SubscriptionStatus.CANCELLED)
    )
    assert await entitlement.check_feature("ws-1", "reranker") is True
    assert await entitlement.check_limit("ws-1", "agent", 10_000) is True


# ── Resource ceiling checkpoints ───────────────────────────────────────────


async def test_create_agent_ceiling_rejects_beyond_plan() -> None:
    from app.agent_config.repository import InMemoryAgentDefinitionRepository
    from app.agent_config.service import AgentDefinitionService
    from app.tools.calculator import CalculatorTool
    from app.tools.registry import ToolRegistry

    repo = _billing_repo_with_seeds()
    entitlement = EntitlementService(repo)
    free_plan = repo._plans_by_name["free"]  # max_agents=3
    await repo.create_subscription(
        _sub(repo, "ws-1", free_plan.id, SubscriptionStatus.ACTIVE)
    )
    service = AgentDefinitionService(
        repository=InMemoryAgentDefinitionRepository(),
        tool_registry=ToolRegistry([CalculatorTool()]),
        entitlement=entitlement,
    )

    for index in range(3):
        await service.create_agent(
            "ws-1", f"agent-{index}", "mock", "", created_by="u1"
        )
    with pytest.raises(ValidationError) as excinfo:
        await service.create_agent("ws-1", "agent-4", "mock", "")
    assert "free" in str(excinfo.value)

    # Legacy workspace (no subscription) is unaffected.
    await service.create_agent("ws-2", "legacy-agent", "mock", "")


async def _add_user(
    user_repo: InMemoryUserRepository, user_id: str, email: str
) -> None:
    from app.auth.users_repository import UserRecord

    await user_repo.create(
        UserRecord(
            id=user_id,
            email=email,
            display_name=user_id,
            password_salt="s",
            password_hash="h",
            status="active",
        )
    )


async def test_add_member_ceiling_rejects_beyond_plan() -> None:
    from app.auth.workspaces_repository import InMemoryWorkspaceRepository

    user_repo = InMemoryUserRepository()
    ws_repo = InMemoryWorkspaceRepository()
    repo = InMemoryBillingRepository(
        [
            Plan(id="p-custom", name="custom", max_members=3),
        ]
    )
    entitlement = EntitlementService(repo)
    service = WorkspaceService(
        workspace_repo=ws_repo, user_repo=user_repo, entitlement=entitlement
    )
    await repo.create_subscription(
        _sub(repo, "ws-1", "p-custom", SubscriptionStatus.ACTIVE)
    )
    await ws_repo.add_member("ws-1", "owner-1", "owner")

    for index in range(2):
        await _add_user(user_repo, f"user-{index}", f"user{index}@test.com")
        await service.add_member("ws-1", "owner-1", f"user{index}@test.com", "member")

    await _add_user(user_repo, "user-3", "user3@test.com")
    with pytest.raises(ValidationError) as excinfo:
        await service.add_member("ws-1", "owner-1", "user3@test.com", "member")
    assert "custom" in str(excinfo.value)


def test_document_ceiling_rejects_beyond_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup()
    _setup_admin(monkeypatch)
    try:
        user_key, ws_id = _register("alice@test.com")
        _subscribe("sk-admin", ws_id, FREE_PLAN_ID)  # max_documents=5

        fake_ingestion = _FakeIngestionService(
            [_document(f"doc-{index}") for index in range(5)]
        )
        fake_queue = _FakeIngestionQueue()
        app.dependency_overrides[provide_rag_ingestion_service] = lambda: fake_ingestion
        app.dependency_overrides[rag_api.get_rag_ingestion_queue] = lambda: fake_queue

        # 5 documents already exist: the 6th upload is refused (422).
        blocked = client.post(
            "/api/v1/rag/documents",
            files={"file": ("notes.txt", b"hello world", "text/plain")},
            headers=_auth(user_key),
        )
        assert blocked.status_code == 422, blocked.text

        # Cancel the subscription → legacy: upload passes through.
        _subscribe("sk-admin", ws_id, FREE_PLAN_ID, status="CANCELLED")
        accepted = client.post(
            "/api/v1/rag/documents",
            files={"file": ("notes.txt", b"hello world", "text/plain")},
            headers=_auth(user_key),
        )
        assert accepted.status_code == 202, accepted.text
    finally:
        _teardown()


def _document(document_id: str) -> DocumentSummary:
    return DocumentSummary(
        document_id=document_id,
        filename=f"{document_id}.txt",
        content_sha256="a" * 64,
        embedding_model="mock",
        embedding_dimensions=768,
        created_at=datetime.now(UTC),
        chunk_count=1,
        text_characters=10,
        safety_verdict="clean",
    )


class _FakeIngestionService:
    def __init__(self, documents: list[DocumentSummary]) -> None:
        self._documents = documents

    async def list_documents(self, *, owner_key_hash: str) -> list[DocumentSummary]:
        del owner_key_hash
        return self._documents


class _FakeIngestionQueue:
    async def submit(
        self,
        content: bytes,
        *,
        filename: str | None,
        owner_key_hash: str,
    ) -> IngestionTask:
        del content
        now = datetime.now(UTC)
        return IngestionTask(
            task_id="task-1",
            document_id=None,
            filename=filename or "document.pdf",
            status=IngestionTaskStatus.QUEUED,
            created_at=now,
            updated_at=now,
            owner_key_hash=owner_key_hash,
        )


# ── Billing / subscription APIs ────────────────────────────────────────────


def test_billing_endpoint_legacy_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup()
    _setup_admin(monkeypatch)
    try:
        user_key, ws_id = _register("alice@test.com")

        resp = client.get("/api/v1/billing", headers=_auth(user_key))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["plan"] is None
        assert body["usage"]["total_tokens"] == 0
        assert body["resources"]["agents"] == {"count": 0, "limit": None}
        assert body["resources"]["documents"] == {"count": 0, "limit": None}
        assert body["resources"]["members"]["count"] == 1
    finally:
        _teardown()


def test_billing_endpoint_with_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup()
    _setup_admin(monkeypatch)
    try:
        user_key, ws_id = _register("alice@test.com")
        _subscribe("sk-admin", ws_id, FREE_PLAN_ID)

        resp = client.get("/api/v1/billing", headers=_auth(user_key))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["plan"]["name"] == "free"
        assert body["plan"]["status"] == "ACTIVE"
        assert body["plan"]["features"] == {"reranker": False, "benchmark": False}
        assert body["plan"]["max_agents"] == 3
        assert body["plan"]["monthly_token_limit"] == 100_000
        assert body["resources"]["agents"]["limit"] == 3
        assert body["resources"]["documents"]["limit"] == 5
    finally:
        _teardown()


def test_billing_endpoint_usage_aggregated(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup()
    _setup_admin(monkeypatch)
    try:
        user_key, ws_id = _register("alice@test.com")
        # Record monthly usage directly into the shared usage repository.
        usage_repo = app.dependency_overrides[provide_usage_service]()._repository  # type: ignore[attr-defined,union-attr]
        usage_repo._records.append(_usage_record(ws_id, 500))  # type: ignore[attr-defined]
        usage_repo._records.append(_usage_record(ws_id, 300))  # type: ignore[attr-defined]

        resp = client.get("/api/v1/billing", headers=_auth(user_key))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["usage"]["total_tokens"] == 800
    finally:
        _teardown()


def _usage_record(workspace_id: str, tokens: int) -> UsageRecord:
    return UsageRecord(
        request_id="r1",
        api_key_hash="key-a",
        workspace_id=workspace_id,
        model="mock",
        total_tokens=tokens,
        prompt_tokens=tokens,
        completion_tokens=0,
        usage_date=datetime.now(UTC).strftime("%Y-%m-%d"),
    )


def test_admin_subscription_assign_and_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup()
    _setup_admin(monkeypatch)
    try:
        _, ws_id = _register("alice@test.com")

        assigned = client.post(
            f"/admin/workspaces/{ws_id}/subscription",
            json={"plan_id": FREE_PLAN_ID},
            headers=_auth("sk-admin"),
        )
        assert assigned.status_code == 200, assigned.text
        body = assigned.json()
        assert body["plan_name"] == "free"
        assert body["status"] == "ACTIVE"
        assert body["workspace_id"] == ws_id

        updated = client.post(
            f"/admin/workspaces/{ws_id}/subscription",
            json={"plan_id": PRO_PLAN_ID, "status": "TRIAL"},
            headers=_auth("sk-admin"),
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["plan_name"] == "pro"
        assert updated.json()["status"] == "TRIAL"
    finally:
        _teardown()


def test_admin_subscription_404s(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup()
    _setup_admin(monkeypatch)
    try:
        _, ws_id = _register("alice@test.com")

        missing_ws = client.post(
            "/admin/workspaces/00000000-0000-0000-0000-000000000000/subscription",
            json={"plan_id": FREE_PLAN_ID},
            headers=_auth("sk-admin"),
        )
        assert missing_ws.status_code == 404

        missing_plan = client.post(
            f"/admin/workspaces/{ws_id}/subscription",
            json={"plan_id": "00000000-0000-0000-0000-0000000000ff"},
            headers=_auth("sk-admin"),
        )
        assert missing_plan.status_code == 404
    finally:
        _teardown()


def test_admin_subscription_requires_admin_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup()
    _setup_admin(monkeypatch)
    try:
        user_key, ws_id = _register("alice@test.com")

        resp = client.post(
            f"/admin/workspaces/{ws_id}/subscription",
            json={"plan_id": FREE_PLAN_ID},
            headers=_auth(user_key),
        )
        assert resp.status_code in (401, 403)
    finally:
        _teardown()


def test_admin_plans_and_subscriptions_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup()
    _setup_admin(monkeypatch)
    try:
        _, ws_id = _register("alice@test.com")
        _subscribe("sk-admin", ws_id, FREE_PLAN_ID)

        plans = client.get("/admin/plans", headers=_auth("sk-admin"))
        assert plans.status_code == 200, plans.text
        names = {plan["name"] for plan in plans.json()}
        assert names == {"free", "pro", "enterprise"}

        subs = client.get("/admin/subscriptions", headers=_auth("sk-admin"))
        assert subs.status_code == 200, subs.text
        assert len(subs.json()) == 1
        assert subs.json()[0]["status"] == "ACTIVE"

        filtered = client.get(
            "/admin/subscriptions?status=EXPIRED", headers=_auth("sk-admin")
        )
        assert filtered.json() == []
    finally:
        _teardown()
