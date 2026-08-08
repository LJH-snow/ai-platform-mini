from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

from app.tools.models import (
    ToolContext as ToolContext,
)  # re-export (defined in tools layer)

type AgentMessageRole = Literal["user", "assistant", "tool"]


class RunStatus(StrEnum):
    """Terminal states produced by an agent run."""

    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class StopReason(StrEnum):
    """Machine-readable reasons explaining why an agent run ended."""

    DIRECT_ANSWER = "direct_answer"
    MAX_STEPS = "max_steps"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    EXTERNAL_CANCELLED = "external_cancelled"
    MODEL_ERROR = "model_error"
    INVALID_DECISION = "invalid_decision"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"


class AgentEventKind(StrEnum):
    """Observable lifecycle events emitted by the runtime."""

    RUN_STARTED = "run_started"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    MODEL_DECISION = "model_decision"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    ANSWER_DELTA = "answer_delta"
    ANSWER = "answer"
    RUN_STOPPED = "run_stopped"


@dataclass(frozen=True)
class AgentMessage:
    """A message visible to the injected model decision function."""

    role: AgentMessageRole
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None


@dataclass(frozen=True)
class ToolCall:
    """A model-requested tool invocation."""

    call_id: str
    name: str
    arguments: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentDecision:
    """The model's next action and optional provider-reported token usage."""

    answer: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    token_usage: int | None = None
    usage_complete: bool = True


@dataclass(frozen=True)
class AgentAnswerChunk:
    """One provider-produced text chunk for an explicitly streamed answer."""

    content: str
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    done: bool = False


@dataclass(frozen=True)
class ToolResult:
    """A normalized tool result that is appended to agent state."""

    call_id: str
    name: str
    content: str
    succeeded: bool
    error: str | None = None
    truncated: bool = False
    cached: bool = False


@dataclass(frozen=True)
class AgentStep:
    """One model decision and all tool results produced by that decision."""

    index: int
    decision: AgentDecision
    tool_results: tuple[ToolResult, ...] = ()


@dataclass
class AgentState:
    """Mutable state carried through one isolated agent run."""

    run_id: str
    user_input: str
    messages: list[AgentMessage] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)
    token_usage: int = 0
    request_id: str | None = None


@dataclass(frozen=True)
class AgentEvent:
    """An immutable event for tracing the runtime without a logging dependency."""

    kind: AgentEventKind
    run_id: str
    sequence: int
    occurred_at: datetime
    step_index: int | None = None
    message: str | None = None
    decision: AgentDecision | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    status: RunStatus | None = None
    stop_reason: StopReason | None = None
    cumulative_token_usage: int | None = None


@dataclass(frozen=True)
class AgentRunResult:
    """The complete outcome of an agent run."""

    run_id: str
    status: RunStatus
    stop_reason: StopReason
    answer: str | None
    state: AgentState
    events: tuple[AgentEvent, ...]
    token_usage: int
    error: str | None = None
