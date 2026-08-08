"""Audit log tests: hooks record, degrade without breaking, admin API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent_config.repository import InMemoryAgentDefinitionRepository
from app.agent_config.service import AgentDefinitionService
from app.audit.service import AuditActor, AuditService, InMemoryAuditRepository
from app.main import app
from app.prompts.repository import InMemoryPromptRepository
from app.prompts.service import PromptRegistryService
from app.tools import CalculatorTool
from app.tools.registry import ToolRegistry


def _actor(workspace_id: str = "ws-1") -> AuditActor:
    return AuditActor(
        workspace_id=workspace_id,
        api_key_hash="key-hash",
        user_id="user-1",
        ip="127.0.0.1",
    )


def _audit() -> tuple[AuditService, InMemoryAuditRepository]:
    repo = InMemoryAuditRepository()
    return AuditService(repository=repo), repo


def _agent_service(
    audit: AuditService | None = None,
) -> AgentDefinitionService:
    return AgentDefinitionService(
        repository=InMemoryAgentDefinitionRepository(),
        tool_registry=ToolRegistry([CalculatorTool()]),
        prompt_registry=None,
        audit=audit,
    )


def _prompt_service(
    audit: AuditService | None = None,
) -> PromptRegistryService:
    return PromptRegistryService(repository=InMemoryPromptRepository(), audit=audit)


async def test_agent_create_without_actor_is_not_recorded() -> None:
    """Hard acceptance: existing callers (no actor) change no behaviour."""
    service = _agent_service(_audit()[0])

    await service.create_agent(workspace_id="ws-1", name="a", model="m", prompt_ref="")

    assert _audit()[1]._events == []


async def test_agent_create_records_snapshot_with_actor() -> None:
    audit, repo = _audit()
    service = _agent_service(audit)

    record, tools = await service.create_agent(
        workspace_id="ws-1",
        name="a",
        model="m",
        prompt_ref="",
        tool_names=["calculator"],
        actor=_actor(),
    )

    assert len(repo._events) == 1
    event = repo._events[0]
    assert event.action == "agent.create"
    assert event.resource_type == "agent"
    assert event.resource_id == record.id
    assert event.workspace_id == "ws-1"
    assert event.api_key_hash == "key-hash"
    assert event.user_id == "user-1"
    assert event.ip == "127.0.0.1"
    assert event.after is not None
    assert event.after["name"] == "a"
    assert event.after["max_steps"] == 10
    assert tools == ["calculator"]


async def test_agent_update_records_before_and_after() -> None:
    audit, repo = _audit()
    service = _agent_service(audit)
    record, _ = await service.create_agent(
        workspace_id="ws-1",
        name="a",
        model="m",
        prompt_ref="",
        actor=_actor(),
    )

    await service.update_agent(
        record.id,
        workspace_id="ws-1",
        max_steps=5,
        enabled=False,
        actor=_actor(),
    )

    update_events = [e for e in repo._events if e.action == "agent.update"]
    assert len(update_events) == 1
    event = update_events[0]
    assert event.before == {
        "name": "a",
        "model": "m",
        "prompt_ref": "",
        "temperature": 0.7,
        "max_steps": 10,
        "enabled": True,
    }
    assert event.after == {
        "name": "a",
        "model": "m",
        "prompt_ref": "",
        "temperature": 0.7,
        "max_steps": 5,
        "enabled": False,
    }


async def test_agent_delete_records_before_snapshot() -> None:
    audit, repo = _audit()
    service = _agent_service(audit)
    record, _ = await service.create_agent(
        workspace_id="ws-1",
        name="a",
        model="m",
        prompt_ref="",
        actor=_actor(),
    )

    deleted = await service.delete_agent(record.id, workspace_id="ws-1", actor=_actor())

    assert deleted is True
    delete_events = [e for e in repo._events if e.action == "agent.delete"]
    assert len(delete_events) == 1
    assert delete_events[0].before is not None
    assert delete_events[0].before["name"] == "a"


async def test_tool_enable_records_before_after() -> None:
    audit, repo = _audit()
    service = _agent_service(audit)

    await service.set_tool_enabled("ws-1", "calculator", False, actor=_actor())

    events = [e for e in repo._events if e.action == "tool.enable"]
    assert len(events) == 1
    assert events[0].before == {"tool_name": "calculator", "enabled": True}
    assert events[0].after == {"tool_name": "calculator", "enabled": False}


async def test_prompt_create_and_activate_records() -> None:
    audit, repo = _audit()
    service = _prompt_service(audit)
    await service.seed(name="tpl", content="v1", workspace_id=None)

    await service.create_version("tpl", "v2", workspace_id=None, actor=_actor())
    await service.activate("tpl", 1, workspace_id=None, actor=_actor())

    actions = [e.action for e in repo._events]
    assert "prompt.create_version" in actions
    assert "prompt.activate" in actions
    activate = next(e for e in repo._events if e.action == "prompt.activate")
    assert activate.resource_id == "tpl@1"
    assert activate.before == {"name": "tpl", "version": 1}
    assert activate.after == {"name": "tpl", "version": 1}


async def test_audit_failure_does_not_break_business() -> None:
    class _BoomRepository:
        async def record(self, event: object) -> None:
            del event
            raise RuntimeError("audit storage down")

        async def list_events(self, **kwargs: object) -> list[object]:
            del kwargs
            return []

    audit = AuditService(repository=_BoomRepository())  # type: ignore[arg-type]
    service = _agent_service(audit)

    # The create succeeds even though the audit write fails.
    record, _ = await service.create_agent(
        workspace_id="ws-1",
        name="a",
        model="m",
        prompt_ref="",
        actor=_actor(),
    )
    assert record.name == "a"


# ── Admin API ────────────────────────────────────────────────────────────────

client = TestClient(app)


def _setup_admin_api(repo: InMemoryAuditRepository) -> None:
    from app.api.auth import _clear_auth_service_caches
    from app.auth.dependencies import _admin_key_hashes, provide_api_key_service
    from app.auth.hash import hash_api_key
    from app.auth.memory_repository import InMemoryAPIKeyRepository
    from app.auth.models import APIKeyRecord
    from app.auth.service import APIKeyService
    from app.core.container import provide_audit_service
    from app.core.settings import get_settings

    _clear_auth_service_caches()
    import os

    os.environ["ADMIN_API_KEYS"] = "sk-admin"
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    get_settings.cache_clear()
    _admin_key_hashes.cache_clear()
    admin = APIKeyRecord(
        key_hash=hash_api_key("sk-admin"), name="admin", status="active"
    )
    app.dependency_overrides[provide_api_key_service] = lambda: APIKeyService(
        repository=InMemoryAPIKeyRepository([admin])
    )
    app.dependency_overrides[provide_audit_service] = lambda: AuditService(
        repository=repo
    )


def _teardown_admin_api() -> None:
    from app.api.auth import _clear_auth_service_caches
    from app.auth.dependencies import _admin_key_hashes
    from app.core.settings import get_settings

    app.dependency_overrides.clear()
    _clear_auth_service_caches()
    get_settings.cache_clear()
    _admin_key_hashes.cache_clear()


def test_admin_audit_api_filters_and_sorts(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    repo = InMemoryAuditRepository()
    asyncio.run(
        repo.record(
            __import__("app.audit.service", fromlist=["AuditEvent"]).AuditEvent(
                action="agent.create",
                resource_type="agent",
                resource_id="a1",
                workspace_id="ws-1",
                api_key_hash="k1",
            )
        )
    )
    asyncio.run(
        repo.record(
            __import__("app.audit.service", fromlist=["AuditEvent"]).AuditEvent(
                action="tool.enable",
                resource_type="tool",
                resource_id="calculator",
                workspace_id="ws-2",
            )
        )
    )
    _setup_admin_api(repo)
    try:
        resp = client.get(
            "/admin/audit-events?action=agent.create",
            headers={"Authorization": "Bearer sk-admin"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["action"] == "agent.create"
        assert body[0]["workspace_id"] == "ws-1"

        all_resp = client.get(
            "/admin/audit-events?limit=200",
            headers={"Authorization": "Bearer sk-admin"},
        )
        assert all_resp.status_code == 200
        assert len(all_resp.json()) == 2

        bad = client.get(
            "/admin/audit-events?limit=999",
            headers={"Authorization": "Bearer sk-admin"},
        )
        assert bad.status_code == 422
    finally:
        _teardown_admin_api()


def test_admin_audit_api_requires_admin_key() -> None:
    _setup_admin_api(InMemoryAuditRepository())
    try:
        resp = client.get(
            "/admin/audit-events",
            headers={"Authorization": "Bearer sk-regular"},
        )
        assert resp.status_code in (401, 403)
    finally:
        _teardown_admin_api()


async def test_agent_update_name_before_snapshot_is_old_value() -> None:
    """Regression: the pre-update snapshot must predate ALL field mutations."""
    audit, repo = _audit()
    service = _agent_service(audit)
    record, _ = await service.create_agent(
        workspace_id="ws-1",
        name="old-name",
        model="m",
        prompt_ref="",
        actor=_actor(),
    )

    await service.update_agent(
        record.id,
        workspace_id="ws-1",
        name="new-name",
        actor=_actor(),
    )

    update_events = [e for e in repo._events if e.action == "agent.update"]
    assert len(update_events) == 1
    assert update_events[0].before is not None
    assert update_events[0].after is not None
    assert update_events[0].before["name"] == "old-name"
    assert update_events[0].after["name"] == "new-name"
