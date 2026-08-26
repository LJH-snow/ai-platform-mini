"""Record dataclasses for the workflow builder (mirror ``app/agent_config/`` style)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class WorkflowRecord:
    """One workflow definition row (``workflows`` table)."""

    id: str = ""
    workspace_id: str = ""
    name: str = ""
    description: str = ""
    status: str = "draft"
    definition: dict[str, object] = field(default_factory=dict)
    version: int = 1
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class WorkflowRunRecord:
    """One workflow execution row (``workflow_builder_runs`` table).

    ``definition`` is the immutable snapshot captured when the run started;
    later definition edits never affect historical runs.
    """

    id: str = ""
    workflow_id: str = ""
    workspace_id: str = ""
    status: str = "running"
    inputs: dict[str, object] = field(default_factory=dict)
    definition: dict[str, object] = field(default_factory=dict)
    node_results: list[dict[str, object]] = field(default_factory=list)
    error: str | None = None
    total_duration_ms: int | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
