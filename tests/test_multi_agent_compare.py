"""M4 comparison runner and benchmark API tests."""

from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient

from app.agent_config.repository import InMemoryAgentDefinitionRepository
from app.agent_config.service import AgentDefinitionService
from app.agents.models import (
    AgentDecision,
    AgentMessage,
    AgentRunResult,
    AgentState,
    AgentStep,
    RunStatus,
    StopReason,
    ToolCall,
)
from app.api.auth import (
    _clear_auth_service_caches,
    provide_user_service,
    provide_workspace_service,
)
from app.api.multi_agent import _provide_multi_agent_compare_runner
from app.auth.dependencies import provide_api_key_service
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKey
from app.auth.service import APIKeyService
from app.auth.user_service import UserService
from app.auth.users_repository import InMemoryUserRepository
from app.auth.workspace_service import WorkspaceService
from app.auth.workspaces_repository import InMemoryWorkspaceRepository
from app.core.container import provide_agent_definition_service
from app.core.context import RequestContext
from app.evals.benchmark_repository import InMemoryBenchmarkRunRepository
from app.evals.multi_agent_compare import MultiAgentCompareRunner
from app.main import app
from app.multi_agent.models import OrchestrationResult, OrchestrationStatus
from app.multi_agent.service import MultiAgentService
from app.schemas.agent import AgentRunRequest
from app.services.agent_service import AgentRunOutcome, AgentService
from app.tools import CalculatorTool
from app.tools.registry import ToolRegistry

client = TestClient(app)


def _agent_outcome() -> AgentRunOutcome:
    state = AgentState(
        run_id="agent-run",
        user_input="calculate",
        messages=[AgentMessage(role="user", content="calculate")],
        steps=[
            AgentStep(
                index=1,
                decision=AgentDecision(
                    tool_calls=(ToolCall(call_id="c1", name="calculator"),)
                ),
            )
        ],
    )
    return AgentRunOutcome(
        result=AgentRunResult(
            run_id=state.run_id,
            status=RunStatus.COMPLETED,
            stop_reason=StopReason.DIRECT_ANSWER,
            answer="42",
            state=state,
            events=(),
            token_usage=2,
        ),
        model="test-model",
        prompt_tokens=1,
        completion_tokens=1,
        estimated_usage=False,
    )


class FakeAgentService:
    """Single-agent lane that always completes with a calculator call."""

    async def run(
        self,
        request: AgentRunRequest,
        *,
        context: object,
        api_key: object,
    ) -> AgentRunOutcome:
        del request, context, api_key
        return _agent_outcome()


class FakeMultiAgentService:
    """Multi-agent lane that always completes without tool calls."""

    async def run(
        self,
        *,
        user_input: str,
        config: object,
        context: object,
        api_key: object,
    ) -> OrchestrationResult:
        del user_input, config, context, api_key
        return OrchestrationResult(
            run_id="multi",
            status=OrchestrationStatus.COMPLETED,
            final_output="report",
            total_token_usage=10,
        )


def _definition_service() -> AgentDefinitionService:
    return AgentDefinitionService(
        repository=InMemoryAgentDefinitionRepository(),
        tool_registry=ToolRegistry([CalculatorTool()]),
    )


def _make_runner(
    def_service: AgentDefinitionService | None = None,
) -> MultiAgentCompareRunner:
    return MultiAgentCompareRunner(
        agent_service=cast(AgentService, FakeAgentService()),
        multi_agent_service=cast(MultiAgentService, FakeMultiAgentService()),
        agent_definition_service=def_service or _definition_service(),
        run_repository=InMemoryBenchmarkRunRepository(),
    )


async def _create_agent(
    def_service: AgentDefinitionService, workspace_id: str = "ws-1"
) -> str:
    await def_service.create_agent(
        workspace_id=workspace_id, name="a", model="m", prompt_ref=""
    )
    return (await def_service.list_agents(workspace_id))[0].id


async def test_compare_runner_aggregates_single_and_multi_metrics() -> None:
    def_service = _definition_service()
    agent_id = await _create_agent(def_service)
    runner = _make_runner(def_service)

    record = await runner.run(
        agent_id,
        workspace_id="ws-1",
        context=RequestContext(request_id="req-1"),
        api_key=APIKey(key="key-hash", name="test"),
    )

    assert record.task_count == 3
    assert record.completed_count == 3
    assert record.task_completion_rate == 1.0
    assert record.tool_call_accuracy == 0.5
    payload = record.metric_payload
    judgement = payload["judgement"]
    assert isinstance(judgement, dict)
    assert judgement["single_completed"] == 3
    assert judgement["multi_completed"] == 3
    assert judgement["multi_tokens_below_plus_40_percent"] is False


async def test_compare_runner_rejects_agent_outside_workspace() -> None:
    import pytest

    def_service = _definition_service()
    agent_id = await _create_agent(def_service, workspace_id="ws-2")
    runner = _make_runner(def_service)

    with pytest.raises(ValueError):
        await runner.run(
            agent_id,
            workspace_id="ws-1",
            context=RequestContext(request_id="req-1"),
            api_key=APIKey(key="key-hash", name="test"),
        )


def _setup_auth() -> tuple[InMemoryUserRepository, InMemoryAPIKeyRepository]:
    """Override auth and comparison dependencies with in-memory backends."""
    user_repo = InMemoryUserRepository()
    ws_repo = InMemoryWorkspaceRepository()
    key_repo = InMemoryAPIKeyRepository([])
    user_svc = UserService(repository=user_repo)
    ws_svc = WorkspaceService(workspace_repo=ws_repo, user_repo=user_repo)
    key_svc = APIKeyService(repository=key_repo)

    def_service = _definition_service()
    runner = _make_runner(def_service)

    _clear_auth_service_caches()
    app.dependency_overrides[provide_user_service] = lambda: user_svc
    app.dependency_overrides[provide_workspace_service] = lambda: ws_svc
    app.dependency_overrides[provide_api_key_service] = lambda: key_svc
    app.dependency_overrides[provide_agent_definition_service] = lambda: def_service
    app.dependency_overrides[_provide_multi_agent_compare_runner] = lambda: runner
    return user_repo, key_repo


def _teardown() -> None:
    from app.core.container import clear_container_cache

    app.dependency_overrides.clear()
    _clear_auth_service_caches()
    clear_container_cache()


def _register(email: str) -> tuple[str, str, str]:
    """Register a user and return (api_key, workspace_id, agent_id)."""
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
    api_key = body["api_key"]
    workspace_id = body["workspace"]["id"]
    agent_resp = client.post(
        "/api/v1/agents",
        json={
            "name": "compare-agent",
            "model": "test-model",
            "prompt_ref": "",
            "tool_names": ["calculator"],
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert agent_resp.status_code == 201, agent_resp.text
    return api_key, workspace_id, agent_resp.json()["id"]


def test_benchmark_api_runs_and_lists() -> None:
    _setup_auth()
    try:
        key_a, ws_a, agent_id = _register("alice@example.com")

        run_resp = client.post(
            "/api/v1/multi-agent/benchmark",
            json={"agent_id": agent_id},
            headers={"Authorization": f"Bearer {key_a}"},
        )
        assert run_resp.status_code == 201, run_resp.text
        body = run_resp.json()
        assert body["workspace_id"] == ws_a
        assert body["task_count"] == 3
        assert body["completed_count"] == 3
        assert "single" in body["metric_payload"]
        assert "multi" in body["metric_payload"]
        assert "judgement" in body["metric_payload"]

        runs_resp = client.get(
            "/api/v1/multi-agent/benchmark/runs",
            headers={"Authorization": f"Bearer {key_a}"},
        )
        assert runs_resp.status_code == 200
        assert len(runs_resp.json()) == 1

        key_b, _, _ = _register("bob@example.com")
        other_resp = client.get(
            "/api/v1/multi-agent/benchmark/runs",
            headers={"Authorization": f"Bearer {key_b}"},
        )
        assert other_resp.status_code == 200
        assert other_resp.json() == []
    finally:
        _teardown()


def test_benchmark_api_unknown_agent_returns_404() -> None:
    _setup_auth()
    try:
        key_a, _, _ = _register("carol@example.com")
        resp = client.post(
            "/api/v1/multi-agent/benchmark",
            json={"agent_id": "missing-agent"},
            headers={"Authorization": f"Bearer {key_a}"},
        )
        assert resp.status_code == 404
    finally:
        _teardown()
