"""Pure validation and evaluation helpers for workflow definitions.

No DB, settings, or workspace dependencies: everything here operates on
plain dataclasses / strings so the engine stays testable in isolation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.workflows.engine.models import (
    NodeType,
    WorkflowDefinition,
    WorkflowNode,
)

_NODE_ID_PATTERN = r"[A-Za-z0-9_-]+"
_VAR_INNER_PATTERN = _NODE_ID_PATTERN + r"(?:\.[A-Za-z0-9_]+)?"
_VARIABLE_PATTERN = r"\{\{\s*(" + _VAR_INNER_PATTERN + r")\s*\}\}"
_VARIABLE_RE = re.compile(_VARIABLE_PATTERN)
_NODE_REFERENCE_RE = re.compile(r"\{\{\s*([A-Za-z0-9_-]+)\.output\s*\}\}")

_CONDITION_PATTERNS = {
    "contains": re.compile("^" + _VARIABLE_PATTERN + r"\s+contains\s+'([^']*)'$"),
    "is_empty": re.compile("^" + _VARIABLE_PATTERN + r"\s+is\s+empty$"),
    "equals": re.compile("^" + _VARIABLE_PATTERN + r"\s*==\s*'([^']*)'$"),
}
_CONDITION_FORM_LABELS = (
    "{{var}} contains 'x'",
    "{{var}} is empty",
    "{{var}} == 'x'",
)


class WorkflowValidationError(ValueError):
    """Raised when a workflow definition fails validation (messages in Chinese)."""


@dataclass(frozen=True)
class _TopologicalOrder:
    ordered_ids: tuple[str, ...]
    index_of: Mapping[str, int]


def validate_definition(definition: WorkflowDefinition) -> None:
    """Validate a workflow definition and raise ``WorkflowValidationError``.

    Checks (fail-fast, Chinese error messages):
      - unique node ids, valid node types, existing edge endpoints
      - at least one ``input`` and one ``output`` node; exactly one input edge
      - DAG (Kahn's algorithm); every node has at most one incoming edge
      - condition branches: existing targets, at most one default branch,
        only the three supported literal expression forms
      - template references: referenced node exists and its topological
        order is strictly earlier than the referencing node
    """
    nodes_by_id = {node.id: node for node in definition.nodes}
    seen_ids: set[str] = set()
    duplicate_ids: list[str] = []
    for node in definition.nodes:
        if node.id in seen_ids:
            duplicate_ids.append(node.id)
        seen_ids.add(node.id)
    if duplicate_ids:
        raise WorkflowValidationError(
            f"节点 id 重复：{', '.join(sorted(set(duplicate_ids)))}"
        )

    for node in definition.nodes:
        if node.type not in NodeType:
            raise WorkflowValidationError(
                f"节点 {node.id} 的 type 不合法：{node.type!r}，"
                f"合法值：{', '.join(t.value for t in NodeType)}"
            )

    for edge in definition.edges:
        if edge.from_node not in nodes_by_id:
            raise WorkflowValidationError(
                f"边 from={edge.from_node} 引用了不存在的节点 {edge.from_node}"
            )
        if edge.to_node not in nodes_by_id:
            raise WorkflowValidationError(
                f"边 to={edge.to_node} 引用了不存在的节点 {edge.to_node}"
            )
        if edge.from_node == edge.to_node:
            raise WorkflowValidationError(
                f"边 {edge.from_node} → {edge.to_node} 不允许自环"
            )

    input_nodes = [node for node in definition.nodes if node.type is NodeType.INPUT]
    output_nodes = [node for node in definition.nodes if node.type is NodeType.OUTPUT]
    if not input_nodes:
        raise WorkflowValidationError("定义中缺少 input 节点（至少需要一个）")
    if not output_nodes:
        raise WorkflowValidationError("定义中缺少 output 节点（至少需要一个）")

    out_degrees: dict[str, int] = {node.id: 0 for node in definition.nodes}
    for edge in definition.edges:
        out_degrees[edge.from_node] += 1
    for node in input_nodes:
        if out_degrees[node.id] != 1:
            raise WorkflowValidationError(
                f"input 节点 {node.id} 的出边数量为 {out_degrees[node.id]}，"
                "必须恰好为 1"
            )

    incoming: dict[str, list[str]] = {node.id: [] for node in definition.nodes}
    for edge in definition.edges:
        incoming[edge.to_node].append(edge.from_node)
    for node in definition.nodes:
        if node.type is NodeType.CONDITION:
            for branch in node.config.get("branches", []):
                if isinstance(branch, Mapping):
                    target = branch.get("target")
                    if isinstance(target, str) and target in nodes_by_id:
                        incoming[target].append(node.id)
    for node in definition.nodes:
        if len(incoming[node.id]) > 1:
            raise WorkflowValidationError(
                f"节点 {node.id} 的入边数量为 {len(incoming[node.id])}，"
                "串行流要求每个节点至多 1 条入边"
            )

    topo = topological_order(definition)
    if topo is None:
        raise WorkflowValidationError("定义存在环，不是 DAG")

    for node in definition.nodes:
        if node.type is NodeType.CONDITION:
            _validate_condition_node(node, nodes_by_id, definition)

    for node in definition.nodes:
        _validate_template_references(node, definition, topo)


def _validate_condition_node(
    node: WorkflowNode,
    nodes_by_id: Mapping[str, WorkflowNode],
    definition: WorkflowDefinition,
) -> None:
    """Validate the branches config of one condition node."""
    branches = node.config.get("branches")
    if not isinstance(branches, Sequence) or isinstance(branches, (str, bytes)):
        raise WorkflowValidationError(
            f"条件节点 {node.id} 的 config.branches 必须是数组"
        )
    if not branches:
        raise WorkflowValidationError(f"条件节点 {node.id} 至少需要一个分支")

    default_seen = False
    for branch in branches:
        if not isinstance(branch, Mapping):
            raise WorkflowValidationError(
                f"条件节点 {node.id} 的分支必须是对象（含 condition/target）"
            )
        target = branch.get("target")
        if not isinstance(target, str) or target not in nodes_by_id:
            raise WorkflowValidationError(
                f"条件节点 {node.id} 的分支 target 不存在：{target!r}"
            )
        condition = branch.get("condition")
        if condition is None:
            if default_seen:
                raise WorkflowValidationError(
                    f"条件节点 {node.id} 有多个默认分支（无 condition 的分支至多一个）"
                )
            default_seen = True
            continue
        if not isinstance(condition, str) or not _is_valid_condition(condition):
            raise WorkflowValidationError(
                f"条件节点 {node.id} 的分支表达式不合法：{condition!r}，"
                f"仅支持：{' / '.join(_CONDITION_FORM_LABELS)}"
            )


def _validate_template_references(
    node: WorkflowNode,
    definition: WorkflowDefinition,
    topo: _TopologicalOrder,
) -> None:
    """Check every ``{{node_id.output}}`` reference exists and is ordered earlier."""
    for value in _walk_strings(node.config):
        for reference in _NODE_REFERENCE_RE.findall(value):
            if reference == node.id:
                raise WorkflowValidationError(f"节点 {node.id} 的模板不能引用自身输出")
            if reference not in topo.index_of:
                raise WorkflowValidationError(
                    f"节点 {node.id} 的模板引用了不存在的节点：{reference}"
                )
            if topo.index_of[reference] >= topo.index_of[node.id]:
                raise WorkflowValidationError(
                    f"节点 {node.id} 的模板引用了拓扑序不早于自身的节点输出："
                    f"{reference}（引用必须指向已执行完成的节点）"
                )


def topological_order(definition: WorkflowDefinition) -> _TopologicalOrder | None:
    """Kahn's algorithm; returns ordering (or None when a cycle exists).

    Condition branch targets count as edges (branches never repeat in
    ``edges`` per the frozen design decision), so they participate in
    ordering and cycle detection too.
    """
    adjacency: dict[str, list[str]] = {node.id: [] for node in definition.nodes}
    in_degree: dict[str, int] = {node.id: 0 for node in definition.nodes}
    for edge in definition.edges:
        adjacency[edge.from_node].append(edge.to_node)
        in_degree[edge.to_node] += 1
    for node in definition.nodes:
        if node.type is NodeType.CONDITION:
            for branch in node.config.get("branches", []):
                if not isinstance(branch, Mapping):
                    continue
                target = branch.get("target")
                if isinstance(target, str) and target in adjacency:
                    adjacency[node.id].append(target)
                    in_degree[target] += 1

    ready = [node.id for node in definition.nodes if in_degree[node.id] == 0]
    ordered: list[str] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(node_id)
        for target in adjacency[node_id]:
            in_degree[target] -= 1
            if in_degree[target] == 0:
                ready.append(target)

    if len(ordered) != len(definition.nodes):
        return None
    return _TopologicalOrder(
        tuple(ordered), {node_id: i for i, node_id in enumerate(ordered)}
    )


def _is_valid_condition(expr: str) -> bool:
    return any(pattern.match(expr) for pattern in _CONDITION_PATTERNS.values())


def _walk_strings(value: object) -> list[str]:
    """Recursively collect every string inside a nested config structure."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        results: list[str] = []
        for item in value.values():
            results.extend(_walk_strings(item))
        return results
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        results = []
        for item in value:
            results.extend(_walk_strings(item))
        return results
    return []


def render_template(template: str, variables: Mapping[str, object]) -> str:
    """Replace ``{{var}}`` placeholders with ``str(variables[var])``.

    Raises ``WorkflowValidationError`` (Chinese message) for undefined variables.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise WorkflowValidationError(f"模板引用了未定义的变量：{name}")
        return str(variables[name])

    return _VARIABLE_RE.sub(replace, template)


def evaluate_condition(expr: str, variables: Mapping[str, object]) -> bool:
    """Evaluate one of the three supported literal condition forms.

    Supported forms (exactly):
      - ``{{var}} contains 'x'``  -> substring containment
      - ``{{var}} is empty``      -> value is empty/None
      - ``{{var}} == 'x'``        -> exact string equality

    Raises ``WorkflowValidationError`` for undefined variables or unknown forms.
    """
    for form, pattern in _CONDITION_PATTERNS.items():
        match = pattern.match(expr)
        if match is None:
            continue
        name = match.group(1)
        if name not in variables:
            raise WorkflowValidationError(f"条件表达式引用了未定义的变量：{name}")
        value = variables[name]
        if form == "contains":
            literal = match.group(2)
            return literal in str(value)
        if form == "is_empty":
            return value is None or str(value) == ""
        literal = match.group(2)
        return str(value) == literal
    raise WorkflowValidationError(
        f"条件表达式不合法：{expr!r}，仅支持：{' / '.join(_CONDITION_FORM_LABELS)}"
    )
