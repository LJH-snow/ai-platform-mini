"""Convert canvas DAG configurations into SupervisorDecision objects."""

from __future__ import annotations

import logging
from typing import Any

from app.multi_agent.models import (
    AgentRole,
    Subtask,
    SupervisorDecision,
)

logger = logging.getLogger(__name__)

_MAX_CANVAS_NODES = 20


class DecisionFactoryError(ValueError):
    """Raised when a DAG configuration cannot be converted to a decision."""


def from_dag(dag_json: dict[str, Any]) -> SupervisorDecision:
    """Convert a canvas DAG JSON into a SupervisorDecision.

    The DAG must contain ``nodes`` (list of node definitions) and
    ``edges`` (list of ``{source, target}`` dependency links).  Each node
    carries its agent role, description, and optional per-node config.
    """
    if not isinstance(dag_json, dict):
        raise DecisionFactoryError("DAG must be a JSON object")

    raw_nodes = dag_json.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise DecisionFactoryError("DAG must contain a non-empty 'nodes' list")

    if len(raw_nodes) > _MAX_CANVAS_NODES:
        raise DecisionFactoryError(f"DAG exceeds maximum of {_MAX_CANVAS_NODES} nodes")

    raw_edges = dag_json.get("edges")
    if not isinstance(raw_edges, list):
        raise DecisionFactoryError("'edges' must be a list")

    # Build dependency map from edges: task -> set of tasks it depends on
    depends_map: dict[str, set[str]] = {node["id"]: set() for node in raw_nodes}
    for edge in raw_edges:
        source = edge.get("source")
        target = edge.get("target")
        if source not in depends_map or target not in depends_map:
            raise DecisionFactoryError(
                f"Edge references unknown node: {source} -> {target}"
            )
        depends_map[target].add(source)

    # Validate no cycles via topological sort attempt
    _validate_acyclic(depends_map)

    subtasks: list[Subtask] = []
    for node in raw_nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise DecisionFactoryError("Each node must have a non-empty 'id'")

        role_str = node.get("role", "custom")
        try:
            role = AgentRole(role_str)
        except ValueError:
            role = AgentRole.CUSTOM

        node_config = node.get("config") or {}
        if not isinstance(node_config, dict):
            raise DecisionFactoryError(f"Node {node_id} 'config' must be an object")

        subtasks.append(
            Subtask(
                id=node_id,
                description=str(node.get("description", "")),
                agent_role=role,
                depends_on=tuple(sorted(depends_map[node_id])),
                input_template="",
                priority=0,
            )
        )

    return SupervisorDecision(
        subtasks=subtasks,
        reasoning="Canvas configuration",
    )


def _validate_acyclic(depends_map: dict[str, set[str]]) -> None:
    """Raise DecisionFactoryError if the dependency graph has a cycle."""
    visited: set[str] = set()
    in_stack: set[str] = set()

    def visit(node: str) -> None:
        if node in in_stack:
            raise DecisionFactoryError("DAG contains a cycle")
        if node in visited:
            return
        in_stack.add(node)
        for dep in depends_map.get(node, set()):
            visit(dep)
        in_stack.discard(node)
        visited.add(node)

    for node in depends_map:
        visit(node)
