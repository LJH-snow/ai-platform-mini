"""Workspace tool enablement + audit trail tests (Sprint B Tool Center)."""

from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient

from app.agent_config.repository import InMemoryAgentDefinitionRepository
from app.agent_config.service import AgentDefinitionService
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
from app.auth.workspaces_repository import InMemoryWorkspaceRepository
from app.core.container import (
    provide_agent_definition_service,
    provide_agent_run_record_service,
)
from app.main import app
from app.tools import CalculatorTool
from app.tools.registry import ToolRegistry

client = TestClient(app)


class _FakeRecordService:
    """Records the audit arguments passed by the agent run persistence."""

    def __init__(self) -> None:
        self.saved: list[dict[str, object]] = []

    async def save(
        self,
        response: object,
        request: object,
        context: object,
        api_key: object,
        model: str | None = None,
        *,
        agent_id: str | None = None,
        prompt_ref: str | None = None,
        prompt_version: int | None = None,
    ) -> None:
        del response, request, context, api_key, model
        self.saved.append(
            {
                "agent_id": agent_id,
                "prompt_ref": prompt_ref,
                "prompt_version": prompt_version,
            }
        )


def _definition_service() -> AgentDefinitionService:
    service = AgentDefinitionService(
        repository=InMemoryAgentDefinitionRepository(),
        tool_registry=ToolRegistry([CalculatorTool()]),
    )
    import asyncio

    # Mirror the lifespan bootstrap: seed built-in tool definitions.
    calculator = CalculatorTool()
    asyncio.run(
        service.seed_tool(
            name=calculator.name,
            description=calculator.description,
            parameters_schema=dict(calculator.input_schema),
            enabled_by_default=True,
            owner="builtin",
        )
    )
    return service


def _setup() -> tuple[AgentDefinitionService, _FakeRecordService]:
    user_repo = InMemoryUserRepository()
    ws_repo = InMemoryWorkspaceRepository()
    key_repo = InMemoryAPIKeyRepository([])
    key_svc = APIKeyService(repository=key_repo)
    def_service = _definition_service()
    record_service = _FakeRecordService()

    _clear_auth_service_caches()
    app.dependency_overrides[provide_user_service] = lambda: UserService(
        repository=user_repo
    )
    app.dependency_overrides[provide_workspace_service] = lambda: WorkspaceService(
        workspace_repo=ws_repo, user_repo=user_repo
    )
    app.dependency_overrides[provide_api_key_service] = lambda: key_svc
    app.dependency_overrides[provide_agent_definition_service] = lambda: def_service
    app.dependency_overrides[provide_agent_run_record_service] = lambda: record_service
    return def_service, record_service


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


def test_tool_list_shows_effective_enablement() -> None:
    _setup()
    try:
        api_key, _ = _register("alice@test.com")

        resp = client.get("/api/v1/tools", headers=_auth(api_key))
        assert resp.status_code == 200
        tools = {t["name"]: t for t in resp.json()}
        assert tools["calculator"]["enabled"] is True
        assert tools["calculator"]["enabled_by_default"] is True
    finally:
        _teardown()


def test_tool_disable_blocks_agent_creation_until_reenabled() -> None:
    def_service, _ = _setup()
    try:
        api_key, ws_id = _register("alice@test.com")

        # Disable the calculator for this workspace.
        resp = client.put(
            "/api/v1/tools/calculator",
            json={"enabled": False},
            headers=_auth(api_key),
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # Creating an agent bound to the disabled tool is rejected.
        agent_resp = client.post(
            "/api/v1/agents",
            json={
                "name": "calc-agent",
                "model": "m",
                "prompt_ref": "",
                "tool_names": ["calculator"],
            },
            headers=_auth(api_key),
        )
        assert agent_resp.status_code == 422
        assert "disabled in this workspace" in agent_resp.text

        # Re-enable and the same creation succeeds.
        client.put(
            "/api/v1/tools/calculator",
            json={"enabled": True},
            headers=_auth(api_key),
        )
        ok_resp = client.post(
            "/api/v1/agents",
            json={
                "name": "calc-agent",
                "model": "m",
                "prompt_ref": "",
                "tool_names": ["calculator"],
            },
            headers=_auth(api_key),
        )
        assert ok_resp.status_code == 201

        # Isolation: another workspace is not affected (no override → default).
        key_b, _ = _register("bob@test.com")
        other_resp = client.get("/api/v1/tools", headers=_auth(key_b))
        tools_b = {t["name"]: t["enabled"] for t in other_resp.json()}
        assert tools_b["calculator"] is True
        assert ws_id
    finally:
        _teardown()


def test_tool_endpoints_reject_unbound_keys() -> None:
    _setup()
    try:
        from app.auth.hash import hash_api_key
        from app.auth.models import APIKeyRecord

        legacy = APIKeyRecord(
            key_hash=hash_api_key("sk-legacy"), name="legacy", status="active"
        )
        key_svc = APIKeyService(repository=InMemoryAPIKeyRepository([legacy]))
        app.dependency_overrides[provide_api_key_service] = lambda: key_svc

        list_resp = client.get("/api/v1/tools", headers=_auth("sk-legacy"))
        assert list_resp.status_code == 200
        assert list_resp.json() == []

        put_resp = client.put(
            "/api/v1/tools/calculator",
            json={"enabled": False},
            headers=_auth("sk-legacy"),
        )
        assert put_resp.status_code == 404
    finally:
        _teardown()


def test_tool_unknown_name_returns_404() -> None:
    _setup()
    try:
        api_key, _ = _register("alice@test.com")
        resp = client.put(
            "/api/v1/tools/not-a-tool",
            json={"enabled": True},
            headers=_auth(api_key),
        )
        assert resp.status_code == 404
    finally:
        _teardown()


# ── Audit trail (roadmap B5) ────────────────────────────────────────────────


def test_agent_run_persists_agent_id_and_prompt_ref() -> None:
    from app.api.agent import _persist_agent_run
    from app.auth.identity import IdentityContext
    from app.auth.models import APIKey
    from app.core.context import RequestContext
    from app.schemas.agent import AgentRunRequest, AgentRunResponse
    from app.services.agent_run_record_service import AgentRunRecordService

    def_service, record_service = _setup()
    try:
        api_key, ws_id = _register("alice@test.com")
        agent_resp = client.post(
            "/api/v1/agents",
            json={
                "name": "audit-agent",
                "model": "m",
                "prompt_ref": "custom_prompt",
                "tool_names": [],
            },
            headers=_auth(api_key),
        )
        agent_id = agent_resp.json()["id"]

        # Seed a prompt template so prompt_ref resolves at run time.
        from app.prompts.repository import InMemoryPromptRepository
        from app.prompts.service import PromptRegistryService

        registry = PromptRegistryService(repository=InMemoryPromptRepository())
        import asyncio

        asyncio.run(registry.seed(name="custom_prompt", content="CUSTOM"))

        asyncio.run(
            _persist_agent_run(
                record_service=cast(AgentRunRecordService, record_service),
                response=cast(AgentRunResponse, _dummy_response()),
                request=AgentRunRequest(message="hi", agent_id=agent_id),
                context=RequestContext(
                    request_id="req-1",
                    identity=IdentityContext(
                        user_id="user-1",
                        workspace_id=ws_id,
                        api_key_id=None,
                        api_key_hash="key-hash",
                        role="admin",
                    ),
                ),
                api_key=cast(APIKey, _dummy_api_key()),
                definition_service=def_service,
                prompt_registry=registry,
            )
        )

        assert record_service.saved[-1]["agent_id"] == agent_id
        assert record_service.saved[-1]["prompt_ref"] == "custom_prompt"
        assert record_service.saved[-1]["prompt_version"] == 1
        assert ws_id
    finally:
        _teardown()


def _dummy_response() -> object:
    from datetime import UTC, datetime

    from app.agents.models import RunStatus, StopReason
    from app.schemas.agent import AgentRunResponse, AgentUsage

    return AgentRunResponse(
        run_id="run-audit-1",
        status=RunStatus.COMPLETED,
        stop_reason=StopReason.DIRECT_ANSWER,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        duration_ms=1.0,
        usage=AgentUsage(
            prompt_tokens=1, completion_tokens=1, total_tokens=2, estimated=False
        ),
    )


def _dummy_api_key() -> object:
    from app.auth.models import APIKey

    return APIKey(key="key-hash", name="test")
