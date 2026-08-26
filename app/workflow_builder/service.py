"""Workflow Builder service — create/update/publish/execute + audit hooks.

Publish follows decision point A: a published workflow is frozen and can
only be edited after an explicit ``unpublish`` (409 otherwise). Runs always
snapshot the current definition so later edits never affect history.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.agent_config.service import AgentDefinitionService
from app.audit.service import AuditActor, AuditService
from app.exceptions.base import ConflictError, ValidationError
from app.tools.registry import ToolRegistry
from app.workflow_builder.execution_context import (
    WorkflowExecutionContext,
    reset_workflow_execution_context,
    set_workflow_execution_context,
)
from app.workflow_builder.models import WorkflowRecord, WorkflowRunRecord
from app.workflow_builder.repository import WorkflowRepository, WorkflowRunRepository
from app.workflows.engine.executor import WorkflowEngine
from app.workflows.engine.models import NodeType, RunStatus, WorkflowDefinition
from app.workflows.engine.validation import (
    WorkflowValidationError,
    validate_definition,
)

if TYPE_CHECKING:
    from app.auth.models import APIKey
    from app.core.context import RequestContext

logger = logging.getLogger(__name__)

STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
RUN_RUNNING = "running"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"
RUN_CANCELLED = "cancelled"

_DEFINITION_PARSE_ERRORS = (KeyError, TypeError, ValueError, AttributeError)


class WorkflowBuilderService:
    """Application boundary for workflow CRUD / publish / execution."""

    def __init__(
        self,
        workflow_repository: WorkflowRepository,
        run_repository: WorkflowRunRepository,
        engine: WorkflowEngine,
        tool_registry: ToolRegistry,
        agent_definition_service: AgentDefinitionService,
        audit: AuditService | None = None,
    ) -> None:
        self._workflow_repo = workflow_repository
        self._run_repo = run_repository
        self._engine = engine
        self._tool_registry = tool_registry
        self._agent_definition_service = agent_definition_service
        self._audit = audit

    # ── CRUD ────────────────────────────────────────────────────────────────

    async def create_workflow(
        self,
        workspace_id: str,
        name: str,
        definition: Mapping[str, Any],
        *,
        description: str = "",
        created_by: str | None = None,
    ) -> WorkflowRecord:
        name = name.strip()
        if not name:
            raise ValidationError("流程名称不能为空")
        parsed = self._parse_definition(definition)
        await self._validate_with_workspace(parsed, workspace_id)
        now = datetime.now(UTC)
        record = WorkflowRecord(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            name=name,
            description=description.strip(),
            status=STATUS_DRAFT,
            definition=deepcopy(parsed.to_dict()),
            version=1,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        return await self._workflow_repo.create_workflow(record)

    async def get_workflow(
        self, workflow_id: str, workspace_id: str
    ) -> WorkflowRecord | None:
        return await self._workflow_repo.get_workflow(workflow_id, workspace_id)

    async def list_workflows(self, workspace_id: str) -> list[WorkflowRecord]:
        return await self._workflow_repo.list_workflows(workspace_id)

    async def update_workflow(
        self,
        workflow_id: str,
        workspace_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        definition: Mapping[str, Any] | None = None,
    ) -> WorkflowRecord | None:
        record = await self._workflow_repo.get_workflow(workflow_id, workspace_id)
        if record is None:
            return None
        if record.status == STATUS_PUBLISHED:
            raise ConflictError(
                "已发布的流程禁止直接修改，请先取消发布（unpublish）再编辑"
            )
        if name is not None:
            cleaned_name = name.strip()
            if not cleaned_name:
                raise ValidationError("流程名称不能为空")
            record.name = cleaned_name
        if description is not None:
            record.description = description.strip()
        if definition is not None:
            parsed = self._parse_definition(definition)
            await self._validate_with_workspace(parsed, workspace_id)
            record.definition = deepcopy(parsed.to_dict())
        record.updated_at = datetime.now(UTC)
        return await self._workflow_repo.update_workflow(record)

    async def publish_workflow(
        self,
        workflow_id: str,
        workspace_id: str,
        *,
        actor: AuditActor | None = None,
    ) -> WorkflowRecord | None:
        record = await self._workflow_repo.get_workflow(workflow_id, workspace_id)
        if record is None:
            return None
        parsed = self._parse_definition(record.definition)
        await self._validate_with_workspace(parsed, workspace_id)
        record.status = STATUS_PUBLISHED
        record.version += 1
        record.definition = deepcopy(parsed.to_dict())  # 冻结快照
        record.updated_at = datetime.now(UTC)
        saved = await self._workflow_repo.publish_workflow(record)
        if saved is not None and self._audit is not None and actor is not None:
            await self._audit.record(
                action="workflow.publish",
                resource_type="workflow",
                resource_id=workflow_id,
                actor=actor,
                after={"version": saved.version, "name": saved.name},
            )
        return saved

    async def unpublish_workflow(
        self, workflow_id: str, workspace_id: str
    ) -> WorkflowRecord | None:
        record = await self._workflow_repo.get_workflow(workflow_id, workspace_id)
        if record is None:
            return None
        record.status = STATUS_DRAFT
        record.updated_at = datetime.now(UTC)
        return await self._workflow_repo.update_workflow(record)

    async def delete_workflow(self, workflow_id: str, workspace_id: str) -> bool:
        record = await self._workflow_repo.get_workflow(workflow_id, workspace_id)
        if record is None:
            return False
        if record.status == STATUS_PUBLISHED:
            raise ConflictError("已发布的流程不能直接删除，请先取消发布（unpublish）")
        return await self._workflow_repo.delete_workflow(workflow_id, workspace_id)

    # ── Runs ────────────────────────────────────────────────────────────────

    async def run_workflow(
        self,
        workflow_id: str,
        workspace_id: str,
        inputs: Mapping[str, object],
        *,
        actor: AuditActor | None = None,
        request_context: RequestContext | None = None,
        api_key: APIKey | None = None,
    ) -> WorkflowRunRecord | None:
        """Execute a workflow, snapshotting the definition into the run.

        Cancellation semantics: a ``CancelledError`` is persisted as a
        ``cancelled`` run and then re-raised so the HTTP boundary observes
        the cancellation (chosen over swallowing it: the caller must know
        the request was interrupted).
        """
        workflow = await self._workflow_repo.get_workflow(workflow_id, workspace_id)
        if workflow is None:
            return None
        definition = self._parse_definition(workflow.definition)
        await self._validate_with_workspace(definition, workspace_id)

        snapshot = deepcopy(definition.to_dict())
        now = datetime.now(UTC)
        run = WorkflowRunRecord(
            id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            workspace_id=workspace_id,
            status=RUN_RUNNING,
            inputs=deepcopy(dict(inputs)),
            definition=snapshot,
            node_results=[],
            created_at=now,
        )
        await self._run_repo.create_run(run)

        identity = request_context.identity if request_context else None
        api_key_hash = (
            api_key.key
            if api_key is not None
            else (identity.api_key_hash if identity is not None else "")
        )
        owner_key_hash = identity.tenant_scope if identity is not None else api_key_hash
        ctx = WorkflowExecutionContext(
            workspace_id=workspace_id,
            api_key_hash=api_key_hash,
            owner_key_hash=owner_key_hash,
            run_id=run.id,
            request_id=request_context.request_id if request_context else None,
            api_key=api_key,
            request_context=request_context,
        )
        token = set_workflow_execution_context(ctx)
        start = time.monotonic()
        try:
            result = await self._engine.run(definition, inputs)
        except asyncio.CancelledError:
            await self._finalize_run(
                run.id,
                status=RUN_CANCELLED,
                node_results=[],
                error=None,
                start=start,
            )
            raise
        finally:
            reset_workflow_execution_context(token)

        if result.status is RunStatus.COMPLETED:
            await self._finalize_run(
                run.id,
                status=RUN_COMPLETED,
                node_results=[item.to_dict() for item in result.node_results],
                error=None,
                start=start,
            )
        else:
            await self._finalize_run(
                run.id,
                status=RUN_FAILED,
                node_results=[item.to_dict() for item in result.node_results],
                error=result.error,
                start=start,
            )

        saved = await self._run_repo.get_run(run.id, workspace_id)
        if saved is not None and self._audit is not None and actor is not None:
            await self._audit.record(
                action="workflow.run",
                resource_type="workflow",
                resource_id=workflow_id,
                actor=actor,
                after={
                    "workflow_id": workflow_id,
                    "run_id": run.id,
                    "status": saved.status,
                },
            )
        return saved

    async def get_run(self, run_id: str, workspace_id: str) -> WorkflowRunRecord | None:
        return await self._run_repo.get_run(run_id, workspace_id)

    async def list_runs(
        self, workflow_id: str, workspace_id: str, limit: int
    ) -> list[WorkflowRunRecord]:
        workflow = await self._workflow_repo.get_workflow(workflow_id, workspace_id)
        if workflow is None:
            return []
        return await self._run_repo.list_runs(workflow_id, limit)

    # ── Validation helpers ──────────────────────────────────────────────────

    @staticmethod
    def _parse_definition(raw: Mapping[str, Any]) -> WorkflowDefinition:
        try:
            return WorkflowDefinition.from_dict(raw)
        except _DEFINITION_PARSE_ERRORS as exc:
            raise ValidationError(f"流程定义格式不合法：{exc}") from exc

    async def _validate_with_workspace(
        self, definition: WorkflowDefinition, workspace_id: str
    ) -> None:
        """Pure definition validation + workspace-dependent checks (422)."""
        try:
            validate_definition(definition)
        except WorkflowValidationError as exc:
            raise ValidationError(str(exc)) from exc

        for node in definition.nodes:
            if node.type is NodeType.TOOL:
                tool_name = node.config.get("tool")
                if not isinstance(tool_name, str) or not tool_name.strip():
                    raise ValidationError(f"tool 节点 {node.id} 缺少 tool 配置")
                if self._tool_registry.get_descriptor(tool_name) is None:
                    raise ValidationError(
                        f"tool 节点 {node.id} 引用了未注册的工具：{tool_name}"
                    )
                if not await self._agent_definition_service.is_tool_enabled(
                    workspace_id, tool_name
                ):
                    raise ValidationError(
                        f"tool 节点 {node.id} 引用的工具在"
                        f"当前工作空间未启用：{tool_name}"
                    )
            elif node.type is NodeType.AGENT:
                agent_id = node.config.get("agent_id")
                if not isinstance(agent_id, str) or not agent_id.strip():
                    raise ValidationError(f"agent 节点 {node.id} 缺少 agent_id 配置")
                agent = await self._agent_definition_service.get_agent(
                    agent_id, workspace_id=workspace_id
                )
                if agent is None:
                    raise ValidationError(
                        f"agent 节点 {node.id} 引用的 Agent 不存在"
                        f"或不属于当前工作空间：{agent_id}"
                    )
                if not agent.enabled:
                    raise ValidationError(
                        f"agent 节点 {node.id} 引用的 Agent 已禁用：{agent_id}"
                    )

    async def _finalize_run(
        self,
        run_id: str,
        *,
        status: str,
        node_results: list[dict[str, object]],
        error: str | None,
        start: float,
    ) -> None:
        existing = await self._run_repo.get_run(run_id)
        if existing is None:  # pragma: no cover - storage invariant
            logger.error("workflow run %s 不存在，无法落最终状态", run_id)
            return
        existing.status = status
        existing.node_results = node_results
        existing.error = error
        existing.total_duration_ms = int((time.monotonic() - start) * 1000)
        existing.completed_at = datetime.now(UTC)
        await self._run_repo.update_run(existing)
