"""Request/response schemas for multi-agent orchestration."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.multi_agent.models import FailurePolicy

StreamEventName = (
    "run_started",
    "subtasks_planned",
    "subtask_started",
    "subtask_completed",
    "subtask_failed",
    "subtask_skipped",
    "subtask_answer_delta",
    "run_completed",
    "run_failed",
    "run_timed_out",
    "run_cancelled",
    "run_budget_exceeded",
    "stream_error",
)


class MultiAgentRunRequest(BaseModel):
    """Request body for a multi-agent run."""

    message: str = Field(
        ..., min_length=1, description="User task to decompose and execute"
    )
    supervisor_model: str | None = Field(
        None, description="Model for supervisor decomposition"
    )
    max_subtasks: int = Field(5, ge=1, le=10, description="Maximum number of subtasks")
    max_concurrency: int = Field(3, ge=1, le=10, description="Maximum parallel agents")
    failure_policy: FailurePolicy = Field(
        FailurePolicy.FAIL_FAST, description="How to handle failed subtasks"
    )
    total_timeout: float | None = Field(
        300.0, ge=1.0, description="Global timeout in seconds"
    )
    total_token_budget: int | None = Field(None, ge=1, description="Total token budget")


class SubtaskResultResponse(BaseModel):
    """Response for a single subtask result."""

    task_id: str
    status: str
    output: str = ""
    error: str | None = None
    error_code: str | None = Field(
        default=None,
        max_length=64,
        description="Finite allowlisted public error code.",
    )
    agent_role: str
    token_usage: int = 0
    steps_taken: int = 0
    duration_ms: int | None = None


class MultiAgentRunResponse(BaseModel):
    """Response for a multi-agent run."""

    run_id: str
    status: str
    final_output: str = Field(default="", max_length=8192)
    subtask_results: list[SubtaskResultResponse] = []
    total_token_usage: int = 0
    error: str | None = None
    error_code: str | None = Field(
        default=None,
        max_length=64,
        description="Finite allowlisted public error code.",
    )
    duration_ms: int | None = None


class SubtaskSummarySchema(BaseModel):
    """Privacy-safe projection of one planned subtask."""

    id: str
    agent_role: str
    description: str = ""
    depends_on: list[str] = Field(default_factory=list)


class SubtaskResultSummarySchema(BaseModel):
    """Privacy-safe projection of one subtask result."""

    task_id: str
    status: str
    agent_role: str = "custom"
    output: str = ""
    error_code: str | None = None
    token_usage: int = 0
    duration_ms: int | None = None


class MultiAgentStreamEvent(BaseModel):
    """Safe SSE projection of one real multi-agent lifecycle event.

    Mirrors the public dict produced by app.multi_agent.events.MultiAgentEvent.
    ``stream_error`` is the only event that may lack a run_id/sequence, since
    it describes a stream setup failure before the run started.
    """

    event: str = Field(..., max_length=64)
    run_id: str | None = Field(default=None, max_length=64)
    request_id: str | None = Field(default=None, max_length=128)
    sequence: int | None = Field(default=None, ge=0)
    occurred_at: datetime | None = None
    task_id: str | None = Field(default=None, max_length=128)
    agent_role: str | None = Field(default=None, max_length=64)
    duration_ms: int | None = Field(default=None, ge=0)
    token_usage: int | None = Field(default=None, ge=0)
    output_summary: str | None = Field(default=None, max_length=256)
    error_code: str | None = Field(default=None, max_length=64)
    subtasks: list[SubtaskSummarySchema] = Field(default_factory=list)
    reasoning: str | None = Field(default=None, max_length=256)
    final_output: str | None = Field(default=None, max_length=8192)
    total_token_usage: int | None = Field(default=None, ge=0)
    subtask_results: list[SubtaskResultSummarySchema] = Field(default_factory=list)
    agent_event_kind: str | None = Field(
        default=None,
        max_length=64,
        description="Kind of the forwarded inner Agent event (e.g. answer_delta).",
    )
    step_index: int | None = Field(default=None, ge=0)


class MultiAgentRunSummary(BaseModel):
    """Tenant-safe summary for the multi-agent run history list."""

    run_id: str
    request_id: str | None = Field(default=None, max_length=128)
    api_key_prefix: str = ""
    api_key_name: str = ""
    status: str
    stop_reason: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    total_tokens: int | None = Field(default=None, ge=0)
    subtask_count: int = Field(default=0, ge=0)


class MultiAgentRunDetail(BaseModel):
    """Tenant-safe detail replay of one multi-agent run."""

    run_id: str
    request_id: str | None = Field(default=None, max_length=128)
    api_key_prefix: str = ""
    api_key_name: str = ""
    status: str
    stop_reason: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    total_tokens: int | None = Field(default=None, ge=0)
    response: dict[str, object] = Field(default_factory=dict)
