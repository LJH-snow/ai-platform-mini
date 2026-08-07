"""Domain models for persistent PDF report workflow runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class WorkflowRunStatus(StrEnum):
    RUNNING = "running"
    PENDING_APPROVAL = "pending_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class WorkflowRunStage(StrEnum):
    STARTING = "starting"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass
class WorkflowRun:
    thread_id: str
    owner_key_hash: str
    status: WorkflowRunStatus
    stage: WorkflowRunStage
    filename: str | None = None
    report_topic: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
