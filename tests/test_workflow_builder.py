"""Sprint E2 P2 — builder repositories, real executors, and service semantics."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from app.agent_config.models import AgentRecord
from app.agent_config.repository import InMemoryAgentDefinitionRepository
from app.agent_config.service import AgentDefinitionService
from app.audit.service import AuditActor, AuditService, InMemoryAuditRepository
from app.exceptions.base import ConflictError, ValidationError
from app.tools.calculator import CalculatorTool
from app.tools.models import ToolExecutionResult, ToolExecutionStatus
from app.tools.registry import ToolRegistry
from app.workflow_builder.executors import (
    AgentNodeExecutor,
    KnowledgeNodeExecutor,
    LlmNodeExecutor,
    ToolNodeExecutor,
)
from app.workflow_builder.models import WorkflowRecord, WorkflowRunRecord
from app.workflow_builder.repository import (
    InMemoryWorkflowRepository,
    InMemoryWorkflowRunRepository,
)
from app.workflow_builder.service import WorkflowBuilderService
from app.workflows.engine.executor import NodeOutput, WorkflowEngine
from app.workflows.engine.models import NodeType, WorkflowNode
from workflow_builder_fakes import (
    TOOL_CALCULATOR,
    WS_A,
    WS_B,
    FakeAgentService,
    FakeChatService,
    FakeRAGService,
    FakeToolExecutor,
    agent_definition_dict,
    definition_dict,
    get_prompt_template,
    run_with_context,
    set_prompt_template,
    tool_definition_dict,
)

# ── Service/engine fixture helpers ──────────────────────────────────────────


def build_service(
    *,
    chat: FakeChatService | None = None,
    rag: FakeRAGService | None = None,
    tool_executor: FakeToolExecutor | None = None,
    agent_service: FakeAgentService | None = None,
    audit: AuditService | None = None,
) -> tuple[
    WorkflowBuilderService,
    AgentDefinitionService,
    InMemoryAgentDefinitionRepository,
    InMemoryWorkflowRepository,
    InMemoryWorkflowRunRepository,
]:
    tool_registry = ToolRegistry([CalculatorTool()])
    agent_def_repo = InMemoryAgentDefinitionRepository()
    agent_svc = AgentDefinitionService(
        repository=agent_def_repo,
        tool_registry=tool_registry,
        audit=audit,
    )
    engine = WorkflowEngine(
        {
            NodeType.LLM: LlmNodeExecutor(chat or FakeChatService()),  # type: ignore[arg-type]
            NodeType.KNOWLEDGE: KnowledgeNodeExecutor(rag),  # type: ignore[arg-type]
            NodeType.TOOL: ToolNodeExecutor(
                tool_executor or FakeToolExecutor()  # type: ignore[arg-type]
            ),
            NodeType.AGENT: AgentNodeExecutor(
                agent_service or FakeAgentService(),  # type: ignore[arg-type]
                agent_svc,
            ),
        }
    )
    workflow_repo = InMemoryWorkflowRepository()
    run_repo = InMemoryWorkflowRunRepository()
    service = WorkflowBuilderService(
        workflow_repository=workflow_repo,
        run_repository=run_repo,
        engine=engine,
        tool_registry=tool_registry,
        agent_definition_service=agent_svc,
        audit=audit,
    )
    return service, agent_svc, agent_def_repo, workflow_repo, run_repo


# ── InMemory repository roundtrip ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_inmemory_workflow_repository_crud_and_isolation() -> None:
    repo = InMemoryWorkflowRepository()
    a = WorkflowRecord(
        id="wf-1",
        workspace_id=WS_A,
        name="A",
        definition=definition_dict(),
        created_at=datetime.now(UTC),
    )
    b = WorkflowRecord(
        id="wf-2",
        workspace_id=WS_B,
        name="B",
        definition=definition_dict(),
        created_at=datetime.now(UTC),
    )
    await repo.create_workflow(a)
    await repo.create_workflow(b)

    assert [r.id for r in await repo.list_workflows(WS_A)] == ["wf-1"]
    assert await repo.get_workflow("wf-1", WS_A) is not None
    assert await repo.get_workflow("wf-1", WS_B) is None

    a.name = "A2"
    updated = await repo.update_workflow(a)
    assert updated is not None and updated.name == "A2"

    a.status = "published"
    a.version = 2
    published = await repo.publish_workflow(a)
    assert published is not None and published.version == 2

    assert await repo.delete_workflow("wf-2", WS_B) is True
    assert await repo.delete_workflow("wf-2", WS_A) is False
    assert await repo.get_workflow("wf-2", WS_B) is None


@pytest.mark.asyncio
async def test_inmemory_run_repository_roundtrip_and_ordering() -> None:
    repo = InMemoryWorkflowRunRepository()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(3):
        await repo.create_run(
            WorkflowRunRecord(
                id=f"run-{index}",
                workflow_id="wf-1",
                workspace_id=WS_A,
                status="completed",
                inputs={"text": f"t{index}"},
                definition=definition_dict(),
                created_at=base.replace(second=index),
            )
        )
    runs = await repo.list_runs("wf-1", limit=2)
    assert [r.id for r in runs] == ["run-2", "run-1"]

    latest = await repo.get_run("run-2", WS_A)
    assert latest is not None and latest.status == "completed"
    assert await repo.get_run("run-2", WS_B) is None

    latest.status = "failed"
    latest.error = "节点 n2 执行失败"
    updated = await repo.update_run(latest)
    assert updated is not None and updated.status == "failed"
    assert updated.error == "节点 n2 执行失败"


# ── Postgres repository roundtrip (integration, skipped by default) ─────────

_SKIP_REASON = "Set INTEGRATION_TEST=1 to run PostgreSQL integration tests"
PG_USER_ID = "11111111-1111-4111-8111-000000000001"
PG_WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
PG_OTHER_WORKSPACE_ID = "22222222-2222-4222-8222-222222222222"

pytestmark_pg = pytest.mark.skipif(
    not os.getenv("INTEGRATION_TEST"),
    reason=_SKIP_REASON,
)


@pytest.fixture()
async def pg_builder_repos() -> AsyncGenerator[tuple[Any, Any], None]:
    from testcontainers.community.postgres import PostgresContainer

    from app.db.init import dispose_db, init_db
    from app.db.session import create_async_session_factory
    from app.db.user_models import UserTable, WorkspaceTable
    from app.workflow_builder.repository import (
        PostgresWorkflowRepository,
        PostgresWorkflowRunRepository,
    )

    with PostgresContainer("postgres:16-alpine") as pg:
        database_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        await init_db(database_url)
        factory = create_async_session_factory()
        async with factory() as session:
            session.add(
                UserTable(
                    id=PG_USER_ID,
                    email="workflow-pg@example.com",
                    display_name="Workflow PG",
                    password_salt="salt",
                    password_hash="hash",
                )
            )
            session.add(
                WorkspaceTable(
                    id=PG_WORKSPACE_ID,
                    name="Workflow PG Workspace",
                    created_by_user_id=PG_USER_ID,
                )
            )
            await session.commit()
        try:
            yield (
                PostgresWorkflowRepository(factory),
                PostgresWorkflowRunRepository(factory),
            )
        finally:
            await dispose_db()


@pytest.mark.asyncio
@pytestmark_pg
async def test_postgres_workflow_repository_roundtrip(
    pg_builder_repos: tuple[Any, Any],
) -> None:
    workflow_repo, _ = pg_builder_repos
    record = WorkflowRecord(
        id="9f7c98ce-0000-4000-8000-000000000001",
        workspace_id=PG_WORKSPACE_ID,
        name="pg-workflow",
        description="desc",
        status="draft",
        definition=definition_dict(),
        version=1,
        created_at=datetime.now(UTC),
    )
    saved = await workflow_repo.create_workflow(record)
    assert saved.id == record.id
    assert saved.definition == record.definition

    found = await workflow_repo.get_workflow(record.id, record.workspace_id)
    assert found is not None and found.name == "pg-workflow"
    assert await workflow_repo.get_workflow(record.id, PG_OTHER_WORKSPACE_ID) is None

    found.version = 2
    found.status = "published"
    published = await workflow_repo.publish_workflow(found)
    assert published is not None and published.version == 2

    assert await workflow_repo.delete_workflow(record.id, record.workspace_id) is True
    assert await workflow_repo.get_workflow(record.id, record.workspace_id) is None


@pytest.mark.asyncio
@pytestmark_pg
async def test_postgres_run_repository_roundtrip(
    pg_builder_repos: tuple[Any, Any],
) -> None:
    workflow_repo, run_repo = pg_builder_repos
    workflow = WorkflowRecord(
        id="9f7c98ce-0000-4000-8000-000000000002",
        workspace_id=PG_WORKSPACE_ID,
        name="pg-flow",
        definition=definition_dict(),
    )
    await workflow_repo.create_workflow(workflow)
    run = WorkflowRunRecord(
        id="9f7c98ce-0000-4000-8000-000000000003",
        workflow_id=workflow.id,
        workspace_id=workflow.workspace_id,
        status="running",
        inputs={"text": "hello"},
        definition=definition_dict(),
        node_results=[],
    )
    saved = await run_repo.create_run(run)
    assert saved.id == run.id

    saved.status = "completed"
    saved.node_results = [{"node_id": "n1", "status": "completed"}]
    saved.total_duration_ms = 12
    saved.completed_at = datetime.now(UTC)
    updated = await run_repo.update_run(saved)
    assert updated is not None and updated.status == "completed"
    assert updated.node_results == [{"node_id": "n1", "status": "completed"}]

    listed = await run_repo.list_runs(workflow.id, limit=10)
    assert [item.id for item in listed] == [run.id]
    assert await run_repo.get_run(run.id, workflow.workspace_id) is not None


# ── Real executors ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_executor_renders_template_and_calls_chat() -> None:
    chat = FakeChatService()
    executor = LlmNodeExecutor(chat)  # type: ignore[arg-type]
    node = WorkflowNode(
        id="n2",
        type=NodeType.LLM,
        config={
            "model": "gpt-test",
            "system_prompt": "你是助手",
            "prompt_template": "问题：{{input.text}}",
        },
    )
    output = await run_with_context(executor, node, {"input.text": "你好"})
    assert output.error is None
    assert output.output == "回答:问题：你好"
    assert chat.requests[-1].model == "gpt-test"
    assert chat.requests[-1].system_prompt == "你是助手"
    assert chat.requests[-1].message == "问题：你好"
    assert output.output_summary == "回答:问题：你好"


@pytest.mark.asyncio
async def test_llm_executor_default_model_and_long_summary_truncated() -> None:
    chat = FakeChatService()
    executor = LlmNodeExecutor(chat)  # type: ignore[arg-type]
    node = WorkflowNode(
        id="n2",
        type=NodeType.LLM,
        config={"prompt_template": "{{input.text}}"},
    )
    long_text = "字" * 500
    output = await run_with_context(executor, node, {"input.text": long_text})
    assert output.error is None
    assert chat.requests[-1].model is None  # ChatService resolves the default
    assert output.output_summary is not None
    assert len(output.output_summary) <= 256


@pytest.mark.asyncio
async def test_llm_executor_missing_template_returns_chinese_error() -> None:
    executor = LlmNodeExecutor(FakeChatService())  # type: ignore[arg-type]
    node = WorkflowNode(id="n2", type=NodeType.LLM, config={})
    output = await run_with_context(executor, node, {})
    assert output.error is not None and "prompt_template" in output.error


@pytest.mark.asyncio
async def test_tool_executor_recursively_renders_arguments() -> None:
    fake_tool = FakeToolExecutor()
    executor = ToolNodeExecutor(fake_tool)  # type: ignore[arg-type]
    node = WorkflowNode(
        id="n2",
        type=NodeType.TOOL,
        config={
            "tool": TOOL_CALCULATOR,
            "arguments_template": {
                "expression": "{{input.text}}",
                "nested": [{"deep": "{{n1.output}}"}],
            },
        },
    )
    output = await run_with_context(
        executor, node, {"input.text": "1+1", "n1.output": "x"}
    )
    assert output.error is None
    tool_name, arguments, context = fake_tool.calls[-1]
    assert tool_name == TOOL_CALCULATOR
    assert arguments == {"expression": "1+1", "nested": [{"deep": "x"}]}
    assert context.run_id == "run-test"
    assert context.metadata["workspace_id"] == WS_A
    assert "1+1" in str(output.output)


@pytest.mark.asyncio
async def test_tool_executor_failure_is_passed_through() -> None:
    fake_tool = FakeToolExecutor(
        result=ToolExecutionResult(
            tool_name=TOOL_CALCULATOR,
            status=ToolExecutionStatus.FAILED,
            output="",
            error_code="tool_execution_failed",
            error_message="计算失败",
        )
    )
    executor = ToolNodeExecutor(fake_tool)  # type: ignore[arg-type]
    node = WorkflowNode(
        id="n2",
        type=NodeType.TOOL,
        config={
            "tool": TOOL_CALCULATOR,
            "arguments_template": {"expression": "{{input.text}}"},
        },
    )
    output = await run_with_context(executor, node, {"input.text": "1+1"})
    assert output.error is not None
    assert "计算失败" in output.error


@pytest.mark.asyncio
async def test_agent_executor_resolves_agent_and_returns_answer() -> None:
    tool_registry = ToolRegistry([CalculatorTool()])
    agent_def_repo = InMemoryAgentDefinitionRepository()
    agent_svc = AgentDefinitionService(
        repository=agent_def_repo, tool_registry=tool_registry
    )
    await agent_def_repo.create_agent(
        AgentRecord(
            id="agent-1",
            workspace_id=WS_A,
            name="测试 Agent",
            model="m",
            prompt_ref="",
            enabled=True,
        )
    )
    fake_agent = FakeAgentService()
    executor = AgentNodeExecutor(fake_agent, agent_svc)  # type: ignore[arg-type]
    node = WorkflowNode(
        id="n2",
        type=NodeType.AGENT,
        config={"agent_id": "agent-1", "prompt": "请处理：{{input.text}}"},
    )
    output = await run_with_context(executor, node, {"input.text": "任务"})
    assert output.error is None
    assert output.output == "agent-answer:agent-1:请处理：任务"
    assert fake_agent.requests[-1].agent_id == "agent-1"  # type: ignore[attr-defined]
    assert fake_agent.requests[-1].message == "请处理：任务"  # type: ignore[attr-defined]
    assert fake_agent.contexts[-1].identity.workspace_id == WS_A  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_agent_executor_rejects_cross_workspace_agent() -> None:
    tool_registry = ToolRegistry([CalculatorTool()])
    agent_def_repo = InMemoryAgentDefinitionRepository()
    agent_svc = AgentDefinitionService(
        repository=agent_def_repo, tool_registry=tool_registry
    )
    await agent_def_repo.create_agent(
        AgentRecord(
            id="agent-b",
            workspace_id=WS_B,
            name="B 的 Agent",
            model="m",
            prompt_ref="",
            enabled=True,
        )
    )
    executor = AgentNodeExecutor(FakeAgentService(), agent_svc)  # type: ignore[arg-type]
    node = WorkflowNode(
        id="n2",
        type=NodeType.AGENT,
        config={"agent_id": "agent-b", "prompt": "{{input.text}}"},
    )
    output = await run_with_context(executor, node, {"input.text": "任务"})
    assert output.error is not None and "不属于当前工作空间" in output.error


@pytest.mark.asyncio
async def test_knowledge_executor_renders_query_and_returns_answer() -> None:
    rag = FakeRAGService()
    executor = KnowledgeNodeExecutor(rag)  # type: ignore[arg-type]
    node = WorkflowNode(
        id="n2",
        type=NodeType.KNOWLEDGE,
        config={"query_template": "关于 {{input.text}} 的资料", "top_k": 5},
    )
    output = await run_with_context(executor, node, {"input.text": "退款"})
    assert output.error is None
    assert output.output == "rag:关于 退款 的资料"
    assert rag.queries == ["关于 退款 的资料"]
    assert rag.owner_key_hashes == ["owner-hash"]


@pytest.mark.asyncio
async def test_knowledge_executor_disabled_rag_returns_chinese_error() -> None:
    executor = KnowledgeNodeExecutor(None)
    node = WorkflowNode(
        id="n2", type=NodeType.KNOWLEDGE, config={"query_template": "{{input.text}}"}
    )
    output = await run_with_context(executor, node, {"input.text": "q"})
    assert output.error is not None and "知识库服务未启用" in output.error


# ── Service semantics ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_workflow_validates_definition() -> None:
    service, _, _, _, _ = build_service()
    record = await service.create_workflow(WS_A, "流程", definition_dict())
    assert record.status == "draft"
    assert record.version == 1

    with pytest.raises(ValidationError, match="缺少 output"):
        await service.create_workflow(
            WS_A,
            "坏流程",
            {
                "nodes": [
                    {"id": "n1", "type": "input", "config": {}},
                    {
                        "id": "n2",
                        "type": "llm",
                        "config": {"prompt_template": "{{input.text}}"},
                    },
                ],
                "edges": [{"from": "n1", "to": "n2"}],
            },
        )
    with pytest.raises(ValidationError, match="流程名称不能为空"):
        await service.create_workflow(WS_A, "  ", definition_dict())


@pytest.mark.asyncio
async def test_publish_bumps_version_freezes_and_audits() -> None:
    audit = AuditService(InMemoryAuditRepository())
    service, _, _, _, _ = build_service(audit=audit)
    record = await service.create_workflow(WS_A, "流程", definition_dict())
    actor = AuditActor(workspace_id=WS_A, api_key_hash="key-hash", user_id="u1")

    published = await service.publish_workflow(record.id, WS_A, actor=actor)
    assert published is not None
    assert published.status == "published"
    assert published.version == 2

    events = await audit.list_events(workspace_id=WS_A, action="workflow.publish")
    assert len(events) == 1
    assert events[0].after == {"version": 2, "name": "流程"}  # type: ignore[comparison-overlap]


@pytest.mark.asyncio
async def test_published_workflow_rejects_update_and_unpublish_unblocks() -> None:
    service, _, _, _, _ = build_service()
    record = await service.create_workflow(WS_A, "流程", definition_dict())
    await service.publish_workflow(record.id, WS_A)

    with pytest.raises(ConflictError, match="已发布"):
        await service.update_workflow(record.id, WS_A, name="新名字")

    back_to_draft = await service.unpublish_workflow(record.id, WS_A)
    assert back_to_draft is not None and back_to_draft.status == "draft"
    updated = await service.update_workflow(record.id, WS_A, name="新名字")
    assert updated is not None and updated.name == "新名字"


@pytest.mark.asyncio
async def test_delete_only_allows_draft() -> None:
    service, _, _, _, _ = build_service()
    record = await service.create_workflow(WS_A, "流程", definition_dict())
    assert await service.delete_workflow(record.id, WS_A) is True

    record2 = await service.create_workflow(WS_A, "流程2", definition_dict())
    await service.publish_workflow(record2.id, WS_A)
    with pytest.raises(ConflictError, match="已发布"):
        await service.delete_workflow(record2.id, WS_A)


@pytest.mark.asyncio
async def test_workspace_dependency_validation_rejects_unregistered_tool() -> None:
    service, _, _, _, _ = build_service()
    bad = tool_definition_dict(tool_name="no_such_tool")
    with pytest.raises(ValidationError, match="未注册的工具"):
        await service.create_workflow(WS_A, "流程", bad)


@pytest.mark.asyncio
async def test_workspace_dependency_validation_rejects_disabled_tool() -> None:
    audit = AuditService(InMemoryAuditRepository())
    service, agent_svc, _, _, _ = build_service(audit=audit)
    await agent_svc.seed_tool(
        name=TOOL_CALCULATOR,
        description="calculator",
        parameters_schema={"type": "object", "properties": {}},
        enabled_by_default=True,
    )
    await agent_svc.set_tool_enabled(WS_A, TOOL_CALCULATOR, enabled=False)
    with pytest.raises(ValidationError, match="未启用"):
        await service.create_workflow(WS_A, "流程", tool_definition_dict())


@pytest.mark.asyncio
async def test_workspace_dependency_validation_rejects_cross_workspace_agent() -> None:
    service, _, agent_def_repo, _, _ = build_service()
    await agent_def_repo.create_agent(
        AgentRecord(
            id="agent-b",
            workspace_id=WS_B,
            name="B",
            model="m",
            prompt_ref="",
            enabled=True,
        )
    )
    with pytest.raises(ValidationError, match="不属于当前工作空间"):
        await service.create_workflow(WS_A, "流程", agent_definition_dict("agent-b"))


@pytest.mark.asyncio
async def test_run_draft_trial_succeeds_and_audits() -> None:
    audit = AuditService(InMemoryAuditRepository())
    service, _, _, _, run_repo = build_service(audit=audit)
    record = await service.create_workflow(WS_A, "流程", definition_dict())
    actor = AuditActor(workspace_id=WS_A, api_key_hash="key-hash")

    run = await service.run_workflow(record.id, WS_A, {"text": "你好"}, actor=actor)
    assert run is not None
    assert run.status == "completed"
    assert run.inputs == {"text": "你好"}
    assert run.error is None
    assert run.total_duration_ms is not None and run.total_duration_ms >= 0
    assert run.completed_at is not None
    node_ids = [item["node_id"] for item in run.node_results]
    assert node_ids == ["n1", "n2", "n3"]
    assert all(item["status"] == "completed" for item in run.node_results)

    events = await audit.list_events(workspace_id=WS_A, action="workflow.run")
    assert len(events) == 1
    after = events[0].after or {}
    assert after["workflow_id"] == record.id
    assert after["run_id"] == run.id
    assert after["status"] == "completed"

    persisted = await run_repo.get_run(run.id, WS_A)
    assert persisted is not None and persisted.status == "completed"


@pytest.mark.asyncio
async def test_run_fails_when_node_fails() -> None:
    service, _, _, _, _ = build_service(
        tool_executor=FakeToolExecutor(
            result=ToolExecutionResult(
                tool_name=TOOL_CALCULATOR,
                status=ToolExecutionStatus.FAILED,
                output="",
                error_code="tool_execution_failed",
                error_message="除零",
            )
        )
    )
    record = await service.create_workflow(WS_A, "流程", tool_definition_dict())
    run = await service.run_workflow(record.id, WS_A, {"text": "1/0"})
    assert run is not None
    assert run.status == "failed"
    assert run.error is not None and "工具 calculator 执行失败" in run.error
    assert run.node_results[-1]["node_id"] == "n2"
    assert run.node_results[-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_run_cancelled_error_is_persisted_and_reraising() -> None:
    class CancellingExecutor:
        async def execute(
            self,
            node: WorkflowNode,
            variables: Mapping[str, object],
            context: Mapping[str, object],
        ) -> NodeOutput:
            del node, variables, context
            raise asyncio.CancelledError

    engine = WorkflowEngine({NodeType.LLM: CancellingExecutor()})
    tool_registry = ToolRegistry([CalculatorTool()])
    agent_svc = AgentDefinitionService(
        repository=InMemoryAgentDefinitionRepository(), tool_registry=tool_registry
    )
    service = WorkflowBuilderService(
        workflow_repository=InMemoryWorkflowRepository(),
        run_repository=InMemoryWorkflowRunRepository(),
        engine=engine,
        tool_registry=tool_registry,
        agent_definition_service=agent_svc,
    )
    record = await service.create_workflow(WS_A, "流程", definition_dict())
    with pytest.raises(asyncio.CancelledError):
        await service.run_workflow(record.id, WS_A, {"text": "x"})

    runs = await service.list_runs(record.id, WS_A, limit=10)
    assert len(runs) == 1
    assert runs[0].status == "cancelled"
    assert runs[0].completed_at is not None


@pytest.mark.asyncio
async def test_run_snapshots_definition_after_publish_and_edit() -> None:
    service, _, _, _, _ = build_service()
    record = await service.create_workflow(WS_A, "流程", definition_dict())
    await service.publish_workflow(record.id, WS_A)
    run1 = await service.run_workflow(record.id, WS_A, {"text": "v1"})
    assert run1 is not None

    await service.unpublish_workflow(record.id, WS_A)
    edited = dict(definition_dict())
    set_prompt_template(edited, "新模板 {{input.text}}")
    await service.update_workflow(record.id, WS_A, definition=edited)
    run2 = await service.run_workflow(record.id, WS_A, {"text": "v2"})
    assert run2 is not None

    # run1 keeps the published snapshot; run2 sees the edited definition.
    assert get_prompt_template(run1.definition) == "{{input.text}}"
    assert get_prompt_template(run2.definition) == "新模板 {{input.text}}"


@pytest.mark.asyncio
async def test_get_run_and_list_runs_are_workspace_scoped() -> None:
    service, _, _, _, _ = build_service()
    record = await service.create_workflow(WS_A, "流程", definition_dict())
    run = await service.run_workflow(record.id, WS_A, {"text": "x"})
    assert run is not None
    assert await service.get_run(run.id, WS_A) is not None
    assert await service.get_run(run.id, WS_B) is None
    assert await service.list_runs(record.id, WS_B, limit=10) == []


@pytest.mark.asyncio
async def test_engine_validation_error_is_converted_to_validation_error() -> None:
    service, _, _, _, _ = build_service()
    bad = definition_dict()
    bad["edges"] = [{"from": "n1", "to": "n1"}]
    with pytest.raises(ValidationError, match="自环"):
        await service.create_workflow(WS_A, "流程", bad)


@pytest.mark.asyncio
async def test_parse_definition_rejects_malformed_json() -> None:
    service, _, _, _, _ = build_service()
    with pytest.raises(ValidationError, match="流程定义格式不合法"):
        await service.create_workflow(WS_A, "流程", {"nodes": "not-a-list"})
