from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.agents.models import RunStatus, StopReason
from app.schemas.chat import ChatMessage

type AgentRAGStatus = Literal[
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


class AgentRunRequest(BaseModel):
    """Validated input for one synchronous Agent Runtime execution."""

    message: str = Field(min_length=1, description="Latest user message.")
    model: str | None = Field(default=None, min_length=1)
    system_prompt: str | None = Field(default=None)
    history: list[ChatMessage] = Field(default_factory=list)
    max_steps: int = Field(default=5, ge=1, le=20)
    timeout_seconds: float = Field(default=30.0, gt=0, le=120.0)
    token_budget: int = Field(default=2048, gt=0, le=32768)


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
    tool_succeeded: bool | None = None
    tool_calls: list[AgentToolCallSummary] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class AgentEventSummary(BaseModel):
    """Safe event summary suitable for a synchronous API response."""

    kind: str
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
    steps: list[AgentStepSummary] = Field(default_factory=list)
    events: list[AgentEventSummary] = Field(default_factory=list)
    usage: AgentUsage
