from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.models import RunStatus, StopReason
from app.schemas.chat import ChatMessage

type AgentRAGStatus = Literal[
    "loading",
    "success_with_sources",
    "no_relevant_sources",
    "knowledge_base_empty",
    "rag_unavailable",
    "embedding_failed",
    "output_unavailable",
    "failed",
]

type AgentRAGErrorCode = Literal[
    "invalid_query",
    "no_relevant_context",
    "knowledge_base_empty",
    "rag_storage_unavailable",
    "embedding_unavailable",
    "embedding_failed",
    "rag_unavailable",
    "output_truncated",
    "output_malformed",
    "failed",
]

type AgentToolErrorCode = Literal[
    "invalid_tool_arguments",
    "tool_permission_denied",
    "tool_timeout",
    "tool_output_too_large",
    "tool_not_found",
    "tool_execution_failed",
]

DEFAULT_AGENT_MAX_STEPS = 4
MAX_AGENT_MAX_STEPS = 20
DEFAULT_AGENT_TIMEOUT_SECONDS = 60.0
MAX_AGENT_TIMEOUT_SECONDS = 120.0
DEFAULT_AGENT_TOKEN_BUDGET = 8192
MAX_AGENT_TOKEN_BUDGET = 16384


class AgentRunRequest(BaseModel):
    """Validated input for one synchronous Agent Runtime execution."""

    message: str = Field(min_length=1, description="Latest user message.")
    model: str | None = Field(default=None, min_length=1)
    system_prompt: str | None = Field(default=None)
    history: list[ChatMessage] = Field(default_factory=list)
    max_steps: int = Field(
        default=DEFAULT_AGENT_MAX_STEPS, ge=1, le=MAX_AGENT_MAX_STEPS
    )
    timeout_seconds: float = Field(
        default=DEFAULT_AGENT_TIMEOUT_SECONDS,
        gt=0,
        le=MAX_AGENT_TIMEOUT_SECONDS,
    )
    token_budget: int = Field(
        default=DEFAULT_AGENT_TOKEN_BUDGET,
        gt=0,
        le=MAX_AGENT_TOKEN_BUDGET,
    )


class AgentRAGReferenceSummary(BaseModel):
    """Safe public projection of one retrieved RAG reference."""

    document_id: str | None = Field(default=None, max_length=256)
    chunk_id: str | None = Field(default=None, max_length=256)
    chunk_index: int | None = Field(default=None, ge=0, le=1_000_000)
    content: str | None = Field(default=None, max_length=1200)
    distance: float | None = Field(default=None, ge=0, le=2)
    truncated: bool = False


class AgentRAGToolSummary(BaseModel):
    """Safe public projection of knowledge_search structured output."""

    status: AgentRAGStatus = "failed"
    warning: str | None = Field(default=None, max_length=256)
    error_code: AgentRAGErrorCode | None = None
    references: list[AgentRAGReferenceSummary] = Field(default_factory=list)


class AgentToolCallSummary(BaseModel):
    """Safe summary of one tool call without raw tool payloads."""

    call_id: str = Field(max_length=128)
    name: str = Field(max_length=128)
    succeeded: bool | None = None
    truncated: bool | None = None
    cached: bool = False
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0, le=120_000)
    argument_count: int | None = Field(default=None, ge=0, le=128)
    input_summary: str | None = Field(default=None, max_length=256)
    output_summary: str | None = Field(default=None, max_length=256)
    result_chars: int | None = Field(default=None, ge=0, le=8192)
    error_code: AgentToolErrorCode | None = None
    error_message: str | None = None
    rag: AgentRAGToolSummary | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class AgentStepSummary(BaseModel):
    """Safe summary of one runtime step without prompt or tool payloads."""

    index: int = Field(ge=1)
    decision_kind: Literal["final_answer", "tool_call", "invalid"]
    tool_names: list[str] = Field(default_factory=list)
    tool_count: int = Field(default=0, ge=0, le=32)
    summary: str | None = Field(default=None, max_length=256)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0, le=120_000)
    tool_succeeded: bool | None = None
    tool_calls: list[AgentToolCallSummary] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class AgentEventSummary(BaseModel):
    """Safe event summary suitable for a synchronous API response."""

    kind: str
    occurred_at: datetime
    step_index: int | None = Field(default=None, ge=1)
    status: RunStatus | None = None
    stop_reason: StopReason | None = None


class AgentUsage(BaseModel):
    """Provider usage collected across model decisions in one run."""

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated: bool


class AgentRunResponse(BaseModel):
    """Public result for one synchronous Agent Runtime execution."""

    run_id: str
    status: RunStatus
    answer: str | None = None
    stop_reason: StopReason
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0, le=120_000)
    steps: list[AgentStepSummary] = Field(default_factory=list)
    events: list[AgentEventSummary] = Field(default_factory=list)
    usage: AgentUsage


class AgentStreamEvent(BaseModel):
    """Safe SSE projection of one real Runtime lifecycle event."""

    event: Literal[
        "run_started",
        "step_started",
        "step_planned",
        "step_completed",
        "tool_started",
        "rag_started",
        "tool_completed",
        "tool_failed",
        "answer_delta",
        "assistant_message",
        "run_completed",
        "run_failed",
        "run_timed_out",
        "run_cancelled",
        "run_stopped",
    ]
    run_id: str
    request_id: str | None = None
    sequence: int
    occurred_at: datetime
    step_index: int | None = Field(default=None, ge=1)
    call_id: str | None = Field(default=None, max_length=128)
    tool_name: str | None = Field(default=None, max_length=128)
    decision_kind: Literal["final_answer", "tool_call", "invalid"] | None = None
    tool_names: list[str] | None = Field(default=None, max_length=32)
    tool_count: int | None = Field(default=None, ge=0, le=32)
    summary: str | None = Field(default=None, max_length=256)
    argument_count: int | None = Field(default=None, ge=0, le=128)
    input_summary: str | None = Field(default=None, max_length=256)
    output_summary: str | None = Field(default=None, max_length=256)
    result_chars: int | None = Field(default=None, ge=0, le=8192)
    status: RunStatus | None = None
    stop_reason: StopReason | None = None
    cumulative_token_usage: int | None = Field(default=None, ge=0)
    answer: str | None = None
    delta: str | None = None
    succeeded: bool | None = None
    cached: bool | None = None
    error_code: AgentToolErrorCode | None = None
    rag: AgentRAGToolSummary | None = None
