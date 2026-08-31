"""Sprint E2 P2 — workflow builder HTTP API tests."""

from __future__ import annotations

from collections.abc import Coroutine, Iterator
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.agent_config.models import AgentRecord
from app.agent_config.repository import InMemoryAgentDefinitionRepository
from app.agent_config.service import AgentDefinitionService
from app.audit.service import AuditService, InMemoryAuditRepository
from app.auth.dependencies import provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKeyRecord
from app.auth.service import APIKeyService
from app.core.container import provide_workflow_builder_service
from app.main import app
from app.tools.calculator import CalculatorTool
from app.tools.registry import ToolRegistry
from app.workflow_builder.executors import (
    AgentNodeExecutor,
    LlmNodeExecutor,
    ToolNodeExecutor,
)
from app.workflow_builder.repository import (
    InMemoryWorkflowRepository,
    InMemoryWorkflowRunRepository,
)
from app.workflow_builder.service import WorkflowBuilderService
from app.workflows.engine.executor import WorkflowEngine
from app.workflows.engine.models import NodeType
from workflow_builder_fakes import (
    WS_A,
    WS_B,
    FakeAgentService,
    FakeChatService,
    FakeToolExecutor,
    agent_definition_dict,
    definition_dict,
    set_prompt_template,
    tool_definition_dict,
)

KEY_A = "sk-ws-a"
KEY_B = "sk-ws-b"
KEY_LEGACY = "sk-legacy"


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _definition() -> dict[str, object]:
    return definition_dict()


@pytest.fixture()
def api_env() -> Iterator[None]:
    key_repo = InMemoryAPIKeyRepository(
        [
            APIKeyRecord(
                key_hash=hash_api_key(KEY_A),
                name="ws-a",
                status="active",
                id="key-a",
                user_id="user-a",
                workspace_id=WS_A,
            ),
            APIKeyRecord(
                key_hash=hash_api_key(KEY_B),
                name="ws-b",
                status="active",
                id="key-b",
                user_id="user-b",
                workspace_id=WS_B,
            ),
            APIKeyRecord(
                key_hash=hash_api_key(KEY_LEGACY),
                name="legacy",
                status="active",
                id="key-legacy",
            ),
        ]
    )
    key_svc = APIKeyService(repository=key_repo)
    app.dependency_overrides[provide_api_key_service] = lambda: key_svc
    yield
    app.dependency_overrides.pop(provide_api_key_service, None)


@pytest.fixture()
def wb_env(
    api_env: None,
) -> Iterator[
    tuple[
        WorkflowBuilderService,
        AuditService,
        AgentDefinitionService,
        InMemoryAgentDefinitionRepository,
    ]
]:
    del api_env
    audit = AuditService(InMemoryAuditRepository())
    tool_registry = ToolRegistry([CalculatorTool()])
    agent_def_repo = InMemoryAgentDefinitionRepository()
    agent_svc = AgentDefinitionService(
        repository=agent_def_repo,
        tool_registry=tool_registry,
        audit=audit,
    )
    engine = WorkflowEngine(
        {
            NodeType.LLM: LlmNodeExecutor(FakeChatService()),  # type: ignore[arg-type]
            NodeType.TOOL: ToolNodeExecutor(FakeToolExecutor()),  # type: ignore[arg-type]
            NodeType.AGENT: AgentNodeExecutor(
                FakeAgentService(),  # type: ignore[arg-type]
                agent_svc,
            ),
        }
    )
    service = WorkflowBuilderService(
        workflow_repository=InMemoryWorkflowRepository(),
        run_repository=InMemoryWorkflowRunRepository(),
        engine=engine,
        tool_registry=tool_registry,
        agent_definition_service=agent_svc,
        audit=audit,
    )
    app.dependency_overrides[provide_workflow_builder_service] = lambda: service
    yield service, audit, agent_svc, agent_def_repo
    app.dependency_overrides.pop(provide_workflow_builder_service, None)


def _create(
    client: TestClient,
    *,
    key: str = KEY_A,
    definition: dict[str, object] | None = None,
    name: str = "测试流程",
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/workflow-builder/workflows",
        headers=_auth(key),
        json={
            "name": name,
            "description": "desc",
            "definition": definition or _definition(),
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _publish(client: TestClient, workflow_id: str, key: str = KEY_A) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/workflow-builder/workflows/{workflow_id}/publish",
        headers=_auth(key),
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _run(
    client: TestClient, workflow_id: str, inputs: dict[str, object], key: str = KEY_A
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/workflow-builder/workflows/{workflow_id}/runs",
        headers=_auth(key),
        json={"inputs": inputs},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


# ── CRUD ────────────────────────────────────────────────────────────────────


def test_create_workflow_success(wb_env: object) -> None:
    del wb_env
    client = TestClient(app)
    created = _create(client)
    assert created["status"] == "draft"
    assert created["version"] == 1
    assert created["workspace_id"] == WS_A
    assert created["name"] == "测试流程"


def test_create_workflow_invalid_definition_returns_422(wb_env: object) -> None:
    del wb_env
    client = TestClient(app)
    bad = _definition()
    bad["nodes"] = cast(list[dict[str, object]], bad["nodes"])[:2]  # 缺少 output 节点
    bad["edges"] = cast(list[dict[str, object]], bad["edges"])[:1]
    response = client.post(
        "/api/v1/workflow-builder/workflows",
        headers=_auth(KEY_A),
        json={"name": "坏流程", "definition": bad},
    )
    assert response.status_code == 422
    assert "缺少 output" in response.json()["message"]


def test_list_workflows_workspace_isolated(wb_env: object) -> None:
    del wb_env
    client = TestClient(app)
    _create(client, name="A 的流程")
    response_b = client.get("/api/v1/workflow-builder/workflows", headers=_auth(KEY_B))
    assert response_b.status_code == 200
    assert response_b.json() == []

    response_a = client.get("/api/v1/workflow-builder/workflows", headers=_auth(KEY_A))
    assert response_a.status_code == 200
    assert [item["name"] for item in response_a.json()] == ["A 的流程"]


def test_get_workflow_cross_workspace_returns_404(wb_env: object) -> None:
    del wb_env
    client = TestClient(app)
    created = _create(client)
    response = client.get(
        f"/api/v1/workflow-builder/workflows/{created['id']}",
        headers=_auth(KEY_B),
    )
    assert response.status_code == 404
    response_a = client.get(
        f"/api/v1/workflow-builder/workflows/{created['id']}",
        headers=_auth(KEY_A),
    )
    assert response_a.status_code == 200


def test_legacy_key_without_workspace_cannot_access(wb_env: object) -> None:
    del wb_env
    client = TestClient(app)
    response = client.get(
        "/api/v1/workflow-builder/workflows", headers=_auth(KEY_LEGACY)
    )
    assert response.status_code == 404


# ── Publish / freeze ────────────────────────────────────────────────────────


def test_publish_increments_version_and_freezes(wb_env: object) -> None:
    del wb_env
    client = TestClient(app)
    created = _create(client)
    published = _publish(client, created["id"])
    assert published["status"] == "published"
    assert published["version"] == 2
    assert published["definition"] == _definition()

    published_again = _publish(client, created["id"])
    assert published_again["version"] == 3


def test_published_update_returns_409(wb_env: object) -> None:
    del wb_env
    client = TestClient(app)
    created = _create(client)
    _publish(client, created["id"])
    response = client.put(
        f"/api/v1/workflow-builder/workflows/{created['id']}",
        headers=_auth(KEY_A),
        json={"name": "改名"},
    )
    assert response.status_code == 409
    assert "已发布" in response.json()["message"]


def test_unpublish_then_update_succeeds(wb_env: object) -> None:
    del wb_env
    client = TestClient(app)
    created = _create(client)
    _publish(client, created["id"])
    unpublish = client.post(
        f"/api/v1/workflow-builder/workflows/{created['id']}/unpublish",
        headers=_auth(KEY_A),
    )
    assert unpublish.status_code == 200
    assert unpublish.json()["status"] == "draft"

    updated = client.put(
        f"/api/v1/workflow-builder/workflows/{created['id']}",
        headers=_auth(KEY_A),
        json={"name": "新名字"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "新名字"
    assert updated.json()["version"] == 2  # 版本号保留，未重新发布不递增


def test_delete_only_draft(wb_env: object) -> None:
    del wb_env
    client = TestClient(app)
    created = _create(client)
    _publish(client, created["id"])
    response = client.delete(
        f"/api/v1/workflow-builder/workflows/{created['id']}",
        headers=_auth(KEY_A),
    )
    assert response.status_code == 409

    client.post(
        f"/api/v1/workflow-builder/workflows/{created['id']}/unpublish",
        headers=_auth(KEY_A),
    )
    response = client.delete(
        f"/api/v1/workflow-builder/workflows/{created['id']}",
        headers=_auth(KEY_A),
    )
    assert response.status_code == 204


# ── Runs ────────────────────────────────────────────────────────────────────


def test_draft_trial_run_succeeds(wb_env: object) -> None:
    del wb_env
    client = TestClient(app)
    created = _create(client)
    run = _run(client, created["id"], {"text": "你好"})
    assert run["status"] == "completed"
    assert run["inputs"] == {"text": "你好"}
    assert [item["node_id"] for item in run["node_results"]] == ["n1", "n2", "n3"]
    assert run["error"] is None
    assert run["total_duration_ms"] is not None
    assert run["completed_at"] is not None


def test_published_run_uses_frozen_snapshot(wb_env: object) -> None:
    del wb_env
    client = TestClient(app)
    created = _create(client)
    _publish(client, created["id"])
    run1 = _run(client, created["id"], {"text": "v1"})

    # 取消发布后编辑定义再试运行：run1 必须保留发布时的冻结快照。
    client.post(
        f"/api/v1/workflow-builder/workflows/{created['id']}/unpublish",
        headers=_auth(KEY_A),
    )
    edited = _definition()
    set_prompt_template(edited, "新模板 {{input.text}}")
    response = client.put(
        f"/api/v1/workflow-builder/workflows/{created['id']}",
        headers=_auth(KEY_A),
        json={"definition": edited},
    )
    assert response.status_code == 200
    run2 = _run(client, created["id"], {"text": "v2"})

    assert (
        run1["definition"]["nodes"][1]["config"]["prompt_template"] == "{{input.text}}"
    )
    assert (
        run2["definition"]["nodes"][1]["config"]["prompt_template"]
        == "新模板 {{input.text}}"
    )


def test_run_history_and_detail(wb_env: object) -> None:
    del wb_env
    client = TestClient(app)
    created = _create(client)
    run1 = _run(client, created["id"], {"text": "一"})
    run2 = _run(client, created["id"], {"text": "二"})

    history = client.get(
        f"/api/v1/workflow-builder/workflows/{created['id']}/runs?limit=1",
        headers=_auth(KEY_A),
    )
    assert history.status_code == 200
    assert [item["id"] for item in history.json()] == [run2["id"]]

    detail = client.get(
        f"/api/v1/workflow-builder/workflows/runs/{run1['id']}",
        headers=_auth(KEY_A),
    )
    assert detail.status_code == 200
    assert detail.json()["id"] == run1["id"]
    assert detail.json()["status"] == "completed"
    assert detail.json()["inputs"] == {"text": "一"}

    cross = client.get(
        f"/api/v1/workflow-builder/workflows/runs/{run1['id']}",
        headers=_auth(KEY_B),
    )
    assert cross.status_code == 404

    missing = client.get(
        "/api/v1/workflow-builder/workflows/runs/00000000-0000-4000-8000-000000000000",
        headers=_auth(KEY_A),
    )
    assert missing.status_code == 404


# ── Workspace-dependent validation ──────────────────────────────────────────


def test_tool_not_enabled_returns_422(
    wb_env: tuple[
        WorkflowBuilderService,
        AuditService,
        AgentDefinitionService,
        InMemoryAgentDefinitionRepository,
    ],
) -> None:
    _, _, agent_svc, _ = wb_env
    client = TestClient(app)
    _run_async(_disable_calculator(agent_svc, WS_A))
    response = client.post(
        "/api/v1/workflow-builder/workflows",
        headers=_auth(KEY_A),
        json={"name": "工具流程", "definition": tool_definition_dict()},
    )
    assert response.status_code == 422
    assert "未启用" in response.json()["message"]


def test_agent_cross_workspace_returns_422(
    wb_env: tuple[
        WorkflowBuilderService,
        AuditService,
        AgentDefinitionService,
        InMemoryAgentDefinitionRepository,
    ],
) -> None:
    _, _, _, agent_def_repo = wb_env
    client = TestClient(app)
    _run_async(
        agent_def_repo.create_agent(
            AgentRecord(
                id="agent-b",
                workspace_id=WS_B,
                name="B 的 Agent",
                model="m",
                prompt_ref="",
                enabled=True,
            )
        )
    )
    response = client.post(
        "/api/v1/workflow-builder/workflows",
        headers=_auth(KEY_A),
        json={"name": "Agent 流程", "definition": agent_definition_dict("agent-b")},
    )
    assert response.status_code == 422
    assert "不属于当前工作空间" in response.json()["message"]


# ── Audit hooks ─────────────────────────────────────────────────────────────


def test_audit_publish_and_run_recorded(
    wb_env: tuple[
        WorkflowBuilderService,
        AuditService,
        AgentDefinitionService,
        InMemoryAgentDefinitionRepository,
    ],
) -> None:
    _, audit, _, _ = wb_env
    client = TestClient(app)
    created = _create(client)
    _publish(client, created["id"])
    _run(client, created["id"], {"text": "x"})

    publish_events = _run_async(
        audit.list_events(workspace_id=WS_A, action="workflow.publish")
    )
    assert len(publish_events) == 1
    assert publish_events[0].after == {"version": 2, "name": "测试流程"}

    run_events = _run_async(audit.list_events(workspace_id=WS_A, action="workflow.run"))
    assert len(run_events) == 1
    after = run_events[0].after or {}
    assert after["workflow_id"] == created["id"]
    assert after["status"] == "completed"
    assert "run_id" in after


async def _disable_calculator(
    agent_svc: AgentDefinitionService, workspace_id: str
) -> None:
    await agent_svc.seed_tool(
        name="calculator",
        description="calculator",
        parameters_schema={"type": "object", "properties": {}},
        enabled_by_default=True,
    )
    await agent_svc.set_tool_enabled(workspace_id, "calculator", enabled=False)


def _run_async[T](coro: Coroutine[object, object, T]) -> T:
    """Run an async setup coroutine from a sync test (no event loop here)."""
    import asyncio

    return asyncio.run(coro)
