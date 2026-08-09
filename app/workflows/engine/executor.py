"""Workflow execution engine.

The engine only depends on the injected ``NodeExecutor`` protocol
implementations; it never imports ChatService/RAGService/ToolExecutor/
AgentService. ``input`` / ``condition`` / ``output`` nodes are handled
natively (透传 / 分支求值 / 模板渲染); the other node kinds
(llm/knowledge/tool/agent) require an injected executor.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from app.workflows.engine.models import (
    NodeResult,
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRunResult,
    truncate_summary,
)
from app.workflows.engine.validation import (
    WorkflowValidationError,
    evaluate_condition,
    render_template,
    topological_order,
    validate_definition,
)


@dataclass(frozen=True)
class NodeOutput:
    """Result produced by one node executor."""

    output: Any = None
    error: str | None = None
    input_summary: str | None = None
    output_summary: str | None = None


@runtime_checkable
class NodeExecutor(Protocol):
    """Injected boundary for side-effecting nodes (llm/knowledge/tool/agent)."""

    async def execute(
        self,
        node: WorkflowNode,
        variables: Mapping[str, object],
        context: Mapping[str, object],
    ) -> NodeOutput:
        """Execute one node and return its output; set ``error`` to fail the run."""
        ...


class WorkflowEngine:
    """Validates and executes workflow definitions in topological order."""

    def __init__(self, node_executors: Mapping[NodeType, NodeExecutor]) -> None:
        self._node_executors = dict(node_executors)

    async def run(
        self,
        definition: WorkflowDefinition,
        inputs: Mapping[str, object],
    ) -> WorkflowRunResult:
        """Validate, then execute the graph; returns an auditable run result.

        Fails fast: any node error stops the run immediately and later
        nodes are never executed.
        """
        validate_definition(definition)
        order = topological_order(definition)
        if order is None:  # pragma: no cover - validate_definition already rejects
            raise WorkflowValidationError("定义存在环，不是 DAG")

        nodes_by_id = {node.id: node for node in definition.nodes}
        adjacency: dict[str, list[str]] = {node.id: [] for node in definition.nodes}
        for edge in definition.edges:
            adjacency[edge.from_node].append(edge.to_node)
        for node in definition.nodes:
            if node.type is NodeType.CONDITION:
                for branch in _branches(node):
                    target = branch.get("target")
                    if isinstance(target, str) and target in adjacency:
                        adjacency[node.id].append(target)

        variables: dict[str, object] = {
            f"input.{key}": value for key, value in inputs.items()
        }
        node_results: list[NodeResult] = []
        active: set[str] = set(nodes_by_id)
        output: Any = None

        for node_id in order.ordered_ids:
            if node_id not in active:
                continue
            node = nodes_by_id[node_id]
            started_at = datetime.now(UTC)
            start = time.monotonic()
            try:
                result = await self._execute_node(node, variables)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_message = f"节点 {node.id} 执行失败：{exc}"
                node_results.append(
                    self._build_result(
                        node,
                        started_at,
                        start,
                        status=NodeStatus.FAILED,
                        error=error_message,
                    )
                )
                return self._failed(inputs, node_results, error_message)

            if result.error is not None:
                error_message = f"节点 {node.id} 执行失败：{result.error}"
                node_results.append(
                    self._build_result(
                        node,
                        started_at,
                        start,
                        status=NodeStatus.FAILED,
                        error=error_message,
                        input_summary=result.input_summary,
                    )
                )
                return self._failed(inputs, node_results, error_message)

            node_results.append(
                self._build_result(
                    node,
                    started_at,
                    start,
                    status=NodeStatus.COMPLETED,
                    output=result.output,
                    input_summary=result.input_summary,
                    output_summary=result.output_summary,
                )
            )
            if node.type is NodeType.OUTPUT:
                output = result.output
            elif node.type is not NodeType.CONDITION:
                variables[f"{node.id}.output"] = str(result.output)

            if node.type is NodeType.CONDITION:
                target = self._selected_target(node, variables)
                active = {target} | _descendants(target, adjacency, set())

        return WorkflowRunResult(
            status=RunStatus.COMPLETED,
            inputs=dict(inputs),
            output=output,
            node_results=tuple(node_results),
        )

    async def _execute_node(
        self,
        node: WorkflowNode,
        variables: Mapping[str, object],
    ) -> NodeOutput:
        if node.type is NodeType.INPUT:
            text = json.dumps(dict(variables_inputs(variables)), ensure_ascii=False)
            return NodeOutput(
                output=text,
                output_summary=truncate_summary(text),
            )
        if node.type is NodeType.CONDITION:
            selected = self._selected_target(node, variables)
            branch = self._branch_for_target(node, selected)
            return NodeOutput(
                output=selected,
                input_summary=truncate_summary(
                    "; ".join(
                        str(branch.get("condition"))
                        for branch in _branches(node)
                        if branch.get("condition") is not None
                    )
                    or "无条件分支",
                ),
                output_summary=truncate_summary(
                    f"选中分支 {branch.get('id', '?')} → {selected}"
                ),
            )
        if node.type is NodeType.OUTPUT:
            template = node.config.get("output_template")
            if not isinstance(template, str):
                raise WorkflowValidationError(
                    f"output 节点 {node.id} 缺少 output_template 配置"
                )
            rendered = render_template(template, variables)
            return NodeOutput(
                output=rendered,
                input_summary=truncate_summary(rendered),
                output_summary=truncate_summary(rendered),
            )

        executor = self._node_executors.get(node.type)
        if executor is None:
            raise WorkflowValidationError(
                f"未注册 {node.type.value} 类型的节点执行器（节点 {node.id}）"
            )
        return await executor.execute(node, variables, {})

    def _selected_target(
        self, node: WorkflowNode, variables: Mapping[str, object]
    ) -> str:
        branches = _branches(node)
        for branch in branches:
            condition = branch.get("condition")
            if condition is None:
                continue
            if evaluate_condition(str(condition), variables):
                return str(branch["target"])
        for branch in branches:
            if branch.get("condition") is None:
                return str(branch["target"])
        raise WorkflowValidationError(
            f"条件节点 {node.id} 没有匹配的分支，且未配置默认分支"
        )

    @staticmethod
    def _branch_for_target(node: WorkflowNode, target: str) -> Mapping[str, object]:
        for branch in _branches(node):
            if branch.get("target") == target:
                return branch
        raise WorkflowValidationError(  # pragma: no cover - validation rejects this
            f"条件节点 {node.id} 找不到 target {target} 对应的分支"
        )

    @staticmethod
    def _build_result(
        node: WorkflowNode,
        started_at: datetime,
        start: float,
        *,
        status: NodeStatus,
        output: Any = None,  # noqa: ANN401 - 节点输出为任意 JSON 值（集成边界）
        error: str | None = None,
        input_summary: str | None = None,
        output_summary: str | None = None,
    ) -> NodeResult:
        duration_ms = round((time.monotonic() - start) * 1000, 2)
        if status is NodeStatus.COMPLETED:
            if input_summary is None:
                input_summary = truncate_summary(
                    json.dumps(dict(node.config), ensure_ascii=False)
                )
            if output_summary is None:
                output_summary = truncate_summary(str(output))
            else:
                output_summary = truncate_summary(output_summary)
        return NodeResult(
            node_id=node.id,
            type=node.type,
            status=status,
            started_at=started_at,
            duration_ms=duration_ms,
            input_summary=input_summary,
            output_summary=output_summary,
            error=error,
        )

    @staticmethod
    def _failed(
        inputs: Mapping[str, object],
        node_results: list[NodeResult],
        error_message: str,
    ) -> WorkflowRunResult:
        return WorkflowRunResult(
            status=RunStatus.FAILED,
            inputs=dict(inputs),
            output=None,
            node_results=tuple(node_results),
            error=error_message,
        )


def _branches(node: WorkflowNode) -> list[Mapping[str, object]]:
    branches = node.config.get("branches")
    if not isinstance(branches, list):
        return []
    return [branch for branch in branches if isinstance(branch, Mapping)]


def _descendants(
    node_id: str,
    adjacency: Mapping[str, list[str]],
    seen: set[str],
) -> set[str]:
    for target in adjacency.get(node_id, []):
        if target not in seen:
            seen.add(target)
            _descendants(target, adjacency, seen)
    return seen


def variables_inputs(variables: Mapping[str, object]) -> Mapping[str, object]:
    """Extract ``input.*`` entries from the runtime variable map."""
    prefix = "input."
    return {
        key[len(prefix) :]: value
        for key, value in variables.items()
        if key.startswith(prefix)
    }
