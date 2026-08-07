"""Agent Benchmark tests — real-execution runner, metrics, IDOR, and API."""

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
from app.auth.dependencies import provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKey, APIKeyRecord
from app.auth.service import APIKeyService
from app.auth.user_service import UserService
from app.auth.users_repository import InMemoryUserRepository
from app.auth.workspace_service import WorkspaceService
from app.auth.workspaces_repository import InMemoryWorkspaceRepository
from app.core.container import (
    provide_agent_benchmark_runner,
    provide_agent_definition_service,
)
from app.core.context import RequestContext
from app.evals.agent_benchmark import AgentBenchmarkRunner
from app.evals.benchmark_repository import InMemoryBenchmarkRunRepository
from app.main import app
from app.schemas.agent import AgentRunRequest
from app.services.agent_service import AgentRunOutcome, AgentService
from app.tools import CalculatorTool
from app.tools.registry import ToolRegistry

client = TestClient(app)


# ── Fakes ────────────────────────────────────────────────────────────────────


class _FakeAgentService:
    """Scripted AgentService: every task completes with one calculator call."""

    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls: list[AgentRunRequest] = []

    async def run(
        self,
        request: AgentRunRequest,
        *,
        context: object,
        api_key: object,
    ) -> AgentRunOutcome:
        del context, api_key
        self.calls.append(request)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("provider unavailable")
        state = AgentState(
            run_id=f"run-{len(self.calls)}",
            user_input=request.message,
            messages=[AgentMessage(role="user", content=request.message)],
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
                token_usage=0,
            ),
            model="test-model",
            prompt_tokens=1,
            completion_tokens=1,
            estimated_usage=False,
        )


def _definition_service() -> AgentDefinitionService:
    return AgentDefinitionService(
        repository=InMemoryAgentDefinitionRepository(),
        tool_registry=ToolRegistry([CalculatorTool()]),
    )


def _api_key() -> APIKey:
    return APIKey(key="key-hash", name="test-key")


def _make_runner(
    agent_service: object,
    def_service: AgentDefinitionService,
) -> AgentBenchmarkRunner:
    return AgentBenchmarkRunner(
        agent_service=cast(AgentService, agent_service),
        agent_definition_service=def_service,
        run_repository=InMemoryBenchmarkRunRepository(),
    )


async def _create_agent(
    def_service: AgentDefinitionService, workspace_id: str = "ws-1"
) -> str:
    await def_service.create_agent(
        workspace_id=workspace_id, name="a", model="m", prompt_ref=""
    )
    return (await def_service.list_agents(workspace_id))[0].id


# ── Runner unit tests ────────────────────────────────────────────────────────


async def test_metrics_derived_from_real_runs() -> None:
    """Tool accuracy counts only tasks whose expected calls all happened."""
    def_service = _definition_service()
    agent_id = await _create_agent(def_service)
    runner = _make_runner(_FakeAgentService(), def_service)

    record = await runner.run(
        agent_id,
        "default",
        workspace_id="ws-1",
        context=RequestContext(request_id="req-1"),
        api_key=_api_key(),
    )

    # default set: 3 tasks, 2 expect calculator (match), 1 expects
    # knowledge_search (no call made) → 2/3 accuracy, 3/3 completed.
    assert record.task_count == 3
    assert record.completed_count == 3
    assert record.tool_call_accuracy == 2 / 3
    assert record.task_completion_rate == 1.0
    assert record.average_steps == 1.0
    assert record.average_latency_ms is not None
    assert record.average_latency_ms > 0
    assert record.id > 0


async def test_task_level_failure_does_not_abort_set() -> None:
    def_service = _definition_service()
    agent_id = await _create_agent(def_service)
    runner = _make_runner(_FakeAgentService(fail_first=True), def_service)

    record = await runner.run(
        agent_id,
        "default",
        workspace_id="ws-1",
        context=RequestContext(request_id="req-1"),
        api_key=_api_key(),
    )

    assert record.task_count == 3
    assert record.completed_count == 2
    assert record.task_completion_rate == 2 / 3
    # Step/latency averages only count completed tasks; the failed first
    # task must not drag them toward zero.
    assert record.average_steps == 1.0
    assert record.average_latency_ms is not None
    assert record.average_latency_ms > 0
    outcomes = record.metric_payload["task_outcomes"]
    assert isinstance(outcomes, list) and len(outcomes) == 3
    assert outcomes[0]["status"] == "error"
    assert outcomes[0]["error"].startswith("RuntimeError:")


async def test_max_steps_optional_does_not_override_definition() -> None:
    """Omitted max_steps keeps definition semantics; explicit value passes through."""
    def_service = _definition_service()
    agent_id = await _create_agent(def_service)
    fake = _FakeAgentService()
    runner = _make_runner(fake, def_service)
    context = RequestContext(request_id="req-1")
    api_key = _api_key()

    await runner.run(
        agent_id, "default", workspace_id="ws-1", context=context, api_key=api_key
    )
    await runner.run(
        agent_id,
        "default",
        workspace_id="ws-1",
        context=context,
        api_key=api_key,
        max_steps=3,
    )

    # First run (calls 0-2): no explicit max_steps → schema default 4
    # (definition wins because model_fields_set does not contain it).
    # Second run (calls 3-5): explicit 3 passes through.
    assert fake.calls[0].max_steps == 4
    assert "max_steps" not in fake.calls[0].model_fields_set
    assert fake.calls[3].max_steps == 3
    assert "max_steps" in fake.calls[3].model_fields_set


async def test_unknown_task_set_raises() -> None:
    import pytest

    def_service = _definition_service()
    agent_id = await _create_agent(def_service)
    runner = _make_runner(_FakeAgentService(), def_service)

    with pytest.raises(ValueError):
        await runner.run(
            agent_id,
            "missing-set",
            workspace_id="ws-1",
            context=RequestContext(request_id="req-1"),
            api_key=_api_key(),
        )


async def test_agent_outside_workspace_is_rejected() -> None:
    import pytest

    def_service = _definition_service()
    agent_id = await _create_agent(def_service, workspace_id="ws-2")
    runner = _make_runner(_FakeAgentService(), def_service)

    with pytest.raises(ValueError):
        await runner.run(
            agent_id,
            "default",
            workspace_id="ws-1",
            context=RequestContext(request_id="req-1"),
            api_key=_api_key(),
        )


async def test_list_runs_filters_by_workspace_and_agent() -> None:
    def_service = _definition_service()
    agent_a = await _create_agent(def_service, workspace_id="ws-1")
    agent_b = await _create_agent(def_service, workspace_id="ws-9")
    repo = InMemoryBenchmarkRunRepository()
    runner = AgentBenchmarkRunner(
        agent_service=cast(AgentService, _FakeAgentService()),
        agent_definition_service=def_service,
        run_repository=repo,
    )
    context = RequestContext(request_id="req-1")
    api_key = _api_key()

    await runner.run(
        agent_a, "default", workspace_id="ws-1", context=context, api_key=api_key
    )
    await runner.run(
        agent_b, "default", workspace_id="ws-9", context=context, api_key=api_key
    )

    ws1_runs = await runner.list_runs("ws-1")
    ws1_agent_runs = await runner.list_runs("ws-1", agent_id=agent_a)
    ws9_runs = await runner.list_runs("ws-9")

    assert len(ws1_runs) == 1
    assert len(ws1_agent_runs) == 1
    assert len(ws9_runs) == 1
    assert ws1_runs[0].workspace_id == "ws-1"
    assert ws9_runs[0].workspace_id == "ws-9"


# ── API tests ────────────────────────────────────────────────────────────────


def _setup_auth() -> tuple[InMemoryUserRepository, InMemoryAPIKeyRepository]:
    """Override auth + benchmark dependencies with in-memory backends."""
    user_repo = InMemoryUserRepository()
    ws_repo = InMemoryWorkspaceRepository()
    key_repo = InMemoryAPIKeyRepository([])
    user_svc = UserService(repository=user_repo)
    ws_svc = WorkspaceService(workspace_repo=ws_repo, user_repo=user_repo)
    key_svc = APIKeyService(repository=key_repo)

    def_service = _definition_service()
    runner = _make_runner(_FakeAgentService(), def_service)

    _clear_auth_service_caches()
    app.dependency_overrides[provide_user_service] = lambda: user_svc
    app.dependency_overrides[provide_workspace_service] = lambda: ws_svc
    app.dependency_overrides[provide_api_key_service] = lambda: key_svc
    app.dependency_overrides[provide_agent_definition_service] = lambda: def_service
    app.dependency_overrides[provide_agent_benchmark_runner] = lambda: runner
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
            "name": "benchmark-agent",
            "model": "test-model",
            "prompt_ref": "",
            "tool_names": ["calculator"],
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert agent_resp.status_code == 201, agent_resp.text
    return api_key, workspace_id, agent_resp.json()["id"]


def test_run_benchmark_and_list_runs_api() -> None:
    _setup_auth()
    try:
        key_a, ws_a, agent_id = _register("alice@test.com")

        run_resp = client.post(
            "/api/v1/benchmarks/run",
            json={"agent_id": agent_id, "task_set": "default"},
            headers={"Authorization": f"Bearer {key_a}"},
        )
        assert run_resp.status_code == 201, run_resp.text
        body = run_resp.json()
        assert body["workspace_id"] == ws_a
        assert body["task_count"] == 3
        assert body["completed_count"] == 3
        assert body["tool_call_accuracy"] == 2 / 3

        runs_resp = client.get(
            "/api/v1/benchmarks/runs",
            headers={"Authorization": f"Bearer {key_a}"},
        )
        assert runs_resp.status_code == 200
        assert len(runs_resp.json()) == 1

        # Another workspace cannot see the run.
        key_b, _, _ = _register("bob@test.com")
        other_resp = client.get(
            "/api/v1/benchmarks/runs",
            headers={"Authorization": f"Bearer {key_b}"},
        )
        assert other_resp.status_code == 200
        assert other_resp.json() == []
    finally:
        _teardown()


def test_run_benchmark_unknown_agent_returns_404() -> None:
    _setup_auth()
    try:
        key_a, _, _ = _register("alice@test.com")
        resp = client.post(
            "/api/v1/benchmarks/run",
            json={"agent_id": "missing-agent", "task_set": "default"},
            headers={"Authorization": f"Bearer {key_a}"},
        )
        assert resp.status_code == 404
    finally:
        _teardown()


def _legacy_key_overrides() -> None:
    """Replace the auth override with a legacy (unbound) key backend."""
    app.dependency_overrides.clear()
    legacy = APIKeyRecord(
        key_hash=hash_api_key("sk-legacy"), name="legacy", status="active"
    )
    key_repo = InMemoryAPIKeyRepository([legacy])
    key_svc = APIKeyService(repository=key_repo)
    app.dependency_overrides[provide_api_key_service] = lambda: key_svc


def test_run_benchmark_legacy_key_without_workspace_returns_404() -> None:
    _setup_auth()
    try:
        _legacy_key_overrides()
        resp = client.post(
            "/api/v1/benchmarks/run",
            json={"agent_id": "whatever", "task_set": "default"},
            headers={"Authorization": "Bearer sk-legacy"},
        )
        assert resp.status_code == 404
    finally:
        _teardown()


def test_list_benchmark_runs_legacy_key_returns_empty() -> None:
    _setup_auth()
    try:
        _legacy_key_overrides()
        resp = client.get(
            "/api/v1/benchmarks/runs",
            headers={"Authorization": "Bearer sk-legacy"},
        )
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        _teardown()
