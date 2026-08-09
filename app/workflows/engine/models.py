"""Domain models for the Sprint E2 workflow builder engine.

This module is deliberately free of any dependency on the rest of the
application (DB, settings, workspace): it only defines plain data
structures plus pure serialization helpers so the engine can be tested
in isolation and reused by later sprints (API + persistence).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

_SUMMARY_MAX_CHARS = 256
_TRUNCATION_MARKER = "...[truncated]"


class NodeType(StrEnum):
    """Node kinds supported by the first wave of the engine."""

    INPUT = "input"
    LLM = "llm"
    KNOWLEDGE = "knowledge"
    TOOL = "tool"
    CONDITION = "condition"
    AGENT = "agent"
    OUTPUT = "output"


class NodeStatus(StrEnum):
    """Per-node execution outcome."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunStatus(StrEnum):
    """Terminal status of a whole workflow run."""

    COMPLETED = "completed"
    FAILED = "failed"


def truncate_summary(value: str, max_chars: int = _SUMMARY_MAX_CHARS) -> str:
    """Collapse whitespace and truncate a summary to at most ``max_chars`` characters.

    Character-count based (safe for CJK text). Mirrors the bounded summary
    philosophy of ``AgentStepSummary``: never persist the raw large payload,
    always a short, readable projection.
    """
    if max_chars <= 0:
        return ""
    compact = " ".join(value.split())
    if len(compact) <= max_chars:
        return compact
    available = max_chars - len(_TRUNCATION_MARKER)
    if available <= 0:
        return _TRUNCATION_MARKER[:max_chars]
    return compact[:available] + _TRUNCATION_MARKER


@dataclass(frozen=True)
class WorkflowNode:
    """One node of a workflow definition."""

    id: str
    type: NodeType
    config: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> WorkflowNode:
        return cls(
            id=str(raw["id"]),
            type=NodeType(str(raw["type"])),
            config=dict(raw.get("config") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "config": dict(self.config),
        }


@dataclass(frozen=True)
class WorkflowEdge:
    """Unconditional ordering edge between two nodes."""

    from_node: str
    to_node: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> WorkflowEdge:
        return cls(from_node=str(raw["from"]), to_node=str(raw["to"]))

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.from_node, "to": self.to_node}


@dataclass(frozen=True)
class WorkflowDefinition:
    """Full node-graph definition (the ``definition`` JSONB of the design doc)."""

    nodes: tuple[WorkflowNode, ...] = ()
    edges: tuple[WorkflowEdge, ...] = ()
    version: int = 1

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> WorkflowDefinition:
        return cls(
            nodes=tuple(WorkflowNode.from_dict(node) for node in raw.get("nodes", [])),
            edges=tuple(WorkflowEdge.from_dict(edge) for edge in raw.get("edges", [])),
            version=int(raw.get("version", 1)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "version": self.version,
        }


@dataclass(frozen=True)
class NodeResult:
    """Auditable per-node execution record (see design doc section 4)."""

    node_id: str
    type: NodeType
    status: NodeStatus
    started_at: datetime
    duration_ms: float
    input_summary: str | None = None
    output_summary: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "type": self.type.value,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "duration_ms": self.duration_ms,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "error": self.error,
        }


@dataclass(frozen=True)
class WorkflowRunResult:
    """Complete outcome of one workflow run."""

    status: RunStatus
    inputs: Mapping[str, Any]
    output: Any
    node_results: tuple[NodeResult, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "inputs": json.dumps(self.inputs, ensure_ascii=False),
            "output": json.dumps(self.output, ensure_ascii=False),
            "node_results": [result.to_dict() for result in self.node_results],
            "error": self.error,
        }
