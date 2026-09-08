"""Multi-agent orchestration event layer for SSE streaming.

Defines the real lifecycle events produced during a multi-agent run. The
event stream is safe by construction: every user-visible text field is
redacted and length-bounded, and every error is mapped to a finite
allowlisted error code. Event emission must never break orchestration, so
the observer protocol is best-effort (exceptions are logged, not raised).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from app.api.redaction import sanitize_public_text

logger = logging.getLogger(__name__)

# Public error codes exposed on the wire (single source of truth). Raw
# str(exc) values, stack traces, and internal paths are never published.
PUBLIC_ERROR_CODES = frozenset(
    {
        "supervisor_failed",
        "subtask_failed",
        "dependency_deadlock",
        "timeout",
        "cancelled",
        "budget_exceeded",
        "internal_error",
        "stream_setup_failed",
    }
)

# Length bounds for user-visible text fields.
_MAX_SUMMARY_CHARS = 256
_MAX_RESULT_CHARS = 8192


class MultiAgentEventKind(StrEnum):
    """Lifecycle event kinds for a multi-agent run."""

    RUN_STARTED = "run_started"
    SUBTASKS_PLANNED = "subtasks_planned"
    SUBTASK_STARTED = "subtask_started"
    SUBTASK_COMPLETED = "subtask_completed"
    SUBTASK_FAILED = "subtask_failed"
    SUBTASK_SKIPPED = "subtask_skipped"
    SUBTASK_STEP_STARTED = "subtask_step_started"
    SUBTASK_STEP_COMPLETED = "subtask_step_completed"
    SUBTASK_TOOL_STARTED = "subtask_tool_started"
    SUBTASK_TOOL_COMPLETED = "subtask_tool_completed"
    SUBTASK_TOOL_FAILED = "subtask_tool_failed"
    SUBTASK_ANSWER_DELTA = "subtask_answer_delta"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_TIMED_OUT = "run_timed_out"
    RUN_CANCELLED = "run_cancelled"
    RUN_BUDGET_EXCEEDED = "run_budget_exceeded"


_TERMINAL_EVENT_KINDS: frozenset[MultiAgentEventKind] = frozenset(
    {
        MultiAgentEventKind.RUN_COMPLETED,
        MultiAgentEventKind.RUN_FAILED,
        MultiAgentEventKind.RUN_TIMED_OUT,
        MultiAgentEventKind.RUN_CANCELLED,
        MultiAgentEventKind.RUN_BUDGET_EXCEEDED,
    }
)


@dataclass(frozen=True)
class SubtaskSummary:
    """Privacy-safe projection of one planned subtask."""

    id: str
    agent_role: str
    agent_id: str | None = None
    description: str = ""
    depends_on: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe public representation."""
        return {
            "id": self.id,
            "agent_role": self.agent_role,
            "agent_id": self.agent_id,
            "description": self.description,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class SubtaskResultSummary:
    """Privacy-safe projection of one subtask result."""

    task_id: str
    status: str
    agent_role: str = "custom"
    output: str = ""
    error_code: str | None = None
    token_usage: int = 0
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe public representation."""
        return {
            "task_id": self.task_id,
            "status": self.status,
            "agent_role": self.agent_role,
            "output": self.output,
            "error_code": self.error_code,
            "token_usage": self.token_usage,
            "duration_ms": self.duration_ms,
        }


def _bounded_summary(value: str) -> str:
    """Redact and truncate a short text field."""
    sanitized = sanitize_public_text(value).replace("\r", " ").replace("\n", " ")
    if len(sanitized) > _MAX_SUMMARY_CHARS:
        sanitized = sanitized[:_MAX_SUMMARY_CHARS]
    return sanitized


def _bounded_result(value: str) -> str:
    """Redact and truncate a longer text field."""
    sanitized = sanitize_public_text(value)
    if len(sanitized) > _MAX_RESULT_CHARS:
        sanitized = sanitized[:_MAX_RESULT_CHARS]
    return sanitized


def public_error_code(error: str | None) -> str | None:
    """Map an internal error to the finite public allowlist."""
    if error is None:
        return None
    return error if error in PUBLIC_ERROR_CODES else "internal_error"


@dataclass
class MultiAgentEvent:
    """One real lifecycle event for a multi-agent run."""

    run_id: str
    kind: MultiAgentEventKind
    sequence: int
    run_source: str = "supervisor"
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    task_id: str | None = None
    agent_role: str | None = None
    duration_ms: int | None = None
    token_usage: int | None = None
    output_summary: str | None = None
    error_code: str | None = None
    subtasks: tuple[SubtaskSummary, ...] = ()
    reasoning: str | None = None
    final_output: str | None = None
    total_token_usage: int | None = None
    subtask_results: tuple[SubtaskResultSummary, ...] = ()
    agent_event_kind: str | None = None
    step_index: int | None = None
    tool_name: str | None = None
    call_id: str | None = None

    @property
    def is_terminal(self) -> bool:
        """Whether this event represents a terminal run state."""
        return self.kind in _TERMINAL_EVENT_KINDS

    def to_public_dict(self) -> dict[str, object]:
        """Return a JSON-safe, redacted representation for the wire."""
        return {
            "run_id": self.run_id,
            "kind": self.kind.value,
            "sequence": self.sequence,
            "run_source": self.run_source,
            "occurred_at": self.occurred_at.isoformat(),
            "task_id": self.task_id,
            "agent_role": self.agent_role,
            "duration_ms": self.duration_ms,
            "token_usage": self.token_usage,
            "output_summary": self.output_summary,
            "error_code": self.error_code,
            "subtasks": [subtask.to_dict() for subtask in self.subtasks],
            "reasoning": self.reasoning,
            "final_output": self.final_output,
            "total_token_usage": self.total_token_usage,
            "subtask_results": [result.to_dict() for result in self.subtask_results],
            "agent_event_kind": self.agent_event_kind,
            "step_index": self.step_index,
            "tool_name": self.tool_name,
            "call_id": self.call_id,
        }


class MultiAgentEventObserver(Protocol):
    """Observer receiving lifecycle events during a multi-agent run."""

    async def on_event(self, event: MultiAgentEvent) -> None: ...


class BestEffortObserver:
    """Wraps a MultiAgentEventObserver so failures never break orchestration.

    Logs any observer exception and continues; the event stream must not be
    able to take down the orchestration it reports on.
    """

    def __init__(self, inner: MultiAgentEventObserver) -> None:
        self._inner = inner

    async def on_event(self, event: MultiAgentEvent) -> None:
        try:
            await self._inner.on_event(event)
        except Exception:
            logger.warning(
                "multi_agent_observer_failed kind=%s run_id=%s",
                event.kind.value,
                event.run_id,
                exc_info=True,
            )


class SequencedObserver:
    """Assigns monotonic sequence numbers to observed events.

    The orchestrator/service emit events without sequence numbers; this
    wrapper stamps each with an increasing integer so consumers can rely on
    a stable ordering and detect drops.
    """

    def __init__(self, inner: MultiAgentEventObserver) -> None:
        self._inner = inner
        self._sequence = 0

    async def on_event(self, event: MultiAgentEvent) -> None:
        event.sequence = self._sequence
        self._sequence += 1
        await self._inner.on_event(event)


class ComposedObserver:
    """Fan-out events to multiple observers in declaration order.

    Used when one run needs to feed both the SSE bridge and an event
    recorder from the same sequenced event stream.
    """

    def __init__(self, observers: tuple[MultiAgentEventObserver, ...]) -> None:
        if not observers:
            raise ValueError("ComposedObserver requires at least one observer")
        self._observers = observers

    async def on_event(self, event: MultiAgentEvent) -> None:
        for observer in self._observers:
            await observer.on_event(event)
