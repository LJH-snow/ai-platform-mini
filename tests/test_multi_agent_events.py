"""Tests for the multi-agent event layer (SSE projections and observers)."""

from __future__ import annotations

import pytest

from app.multi_agent.events import (
    BestEffortObserver,
    MultiAgentEvent,
    MultiAgentEventKind,
    SequencedObserver,
    SubtaskResultSummary,
    SubtaskSummary,
    public_error_code,
    sanitize_public_text,
)


class TestEventPublicDict:
    """Events project to a JSON-safe, redacted public dict."""

    def test_run_started_projection(self) -> None:
        event = MultiAgentEvent(
            run_id="run-1",
            kind=MultiAgentEventKind.RUN_STARTED,
            sequence=0,
        )
        data = event.to_public_dict()
        assert data["run_id"] == "run-1"
        assert data["kind"] == "run_started"
        assert data["sequence"] == 0
        assert data["occurred_at"] == event.occurred_at.isoformat()
        assert data["subtasks"] == []
        assert data["subtask_results"] == []

    def test_subtasks_planned_projection(self) -> None:
        event = MultiAgentEvent(
            run_id="run-1",
            kind=MultiAgentEventKind.SUBTASKS_PLANNED,
            sequence=1,
            reasoning="Split into research and writing",
            subtasks=(
                SubtaskSummary(id="t1", agent_role="research", description="Research"),
                SubtaskSummary(
                    id="t2",
                    agent_role="writer",
                    description="Write",
                    depends_on=("t1",),
                ),
            ),
        )
        data = event.to_public_dict()
        assert data["reasoning"] == "Split into research and writing"
        subtasks = data["subtasks"]
        assert isinstance(subtasks, list)
        assert subtasks[0] == {
            "id": "t1",
            "agent_role": "research",
            "agent_id": None,
            "description": "Research",
            "depends_on": [],
        }
        second = subtasks[1]
        assert isinstance(second, dict)
        assert second["depends_on"] == ["t1"]

    def test_terminal_projection_includes_summary(self) -> None:
        event = MultiAgentEvent(
            run_id="run-1",
            kind=MultiAgentEventKind.RUN_COMPLETED,
            sequence=3,
            final_output="final answer",
            total_token_usage=42,
            duration_ms=120,
            subtask_results=(
                SubtaskResultSummary(
                    task_id="t1", status="completed", agent_role="research"
                ),
            ),
        )
        data = event.to_public_dict()
        assert data["final_output"] == "final answer"
        assert data["total_token_usage"] == 42
        assert data["duration_ms"] == 120
        results = data["subtask_results"]
        assert isinstance(results, list)
        first = results[0]
        assert isinstance(first, dict)
        assert first["task_id"] == "t1"
        assert event.is_terminal

    def test_non_terminal_event_flag(self) -> None:
        event = MultiAgentEvent(
            run_id="run-1",
            kind=MultiAgentEventKind.SUBTASK_STARTED,
            sequence=2,
            task_id="t1",
        )
        assert not event.is_terminal


class TestSequencedObserver:
    """SequencedObserver assigns monotonic sequence numbers."""

    @pytest.mark.asyncio
    async def test_assigns_increasing_sequence(self) -> None:
        observed: list[int] = []

        class Sink:
            async def on_event(self, event: MultiAgentEvent) -> None:
                observed.append(event.sequence)

        seq = SequencedObserver(Sink())
        for kind in (
            MultiAgentEventKind.RUN_STARTED,
            MultiAgentEventKind.SUBTASKS_PLANNED,
            MultiAgentEventKind.RUN_COMPLETED,
        ):
            await seq.on_event(MultiAgentEvent(run_id="r", kind=kind, sequence=0))
        assert observed == [0, 1, 2]


class TestBestEffortObserver:
    """BestEffortObserver swallows observer failures and keeps the run going."""

    @pytest.mark.asyncio
    async def test_observer_exception_does_not_propagate(self) -> None:
        class Boom:
            async def on_event(self, event: MultiAgentEvent) -> None:
                raise RuntimeError("observer broke")

        wrapper = BestEffortObserver(Boom())
        # Must not raise.
        await wrapper.on_event(
            MultiAgentEvent(
                run_id="r", kind=MultiAgentEventKind.RUN_STARTED, sequence=0
            )
        )


class TestErrorCode:
    """Raw errors are mapped to the finite public allowlist."""

    def test_known_error_passthrough(self) -> None:
        assert public_error_code("subtask_failed") == "subtask_failed"
        assert public_error_code("timeout") == "timeout"
        assert public_error_code(None) is None

    def test_unknown_error_becomes_internal_error(self) -> None:
        assert public_error_code("FileNotFoundError: /etc/secret") == "internal_error"


class TestRedaction:
    """Public text redaction strips credentials and internal paths."""

    def test_redacts_api_key_assignment(self) -> None:
        sanitized = sanitize_public_text("token=sk-secret1234567890 leak")
        assert "sk-secret1234567890" not in sanitized
        assert "[redacted]" in sanitized

    def test_redacts_internal_path(self) -> None:
        # A bare path reference (not a stack-trace line) hits the internal-path
        # redaction pattern rather than the stack-trace line pattern.
        sanitized = sanitize_public_text("found under /home/user/secret/app.py here")
        assert "/home/user/secret/app.py" not in sanitized
        assert "[internal path redacted]" in sanitized


def test_terminal_kind_set_matches_exposed_statuses() -> None:
    """Every exposed terminal status maps to a terminal event kind."""
    assert MultiAgentEventKind.RUN_COMPLETED.value == "run_completed"
    assert MultiAgentEventKind.RUN_FAILED.value == "run_failed"
    assert MultiAgentEventKind.RUN_TIMED_OUT.value == "run_timed_out"
    assert MultiAgentEventKind.RUN_CANCELLED.value == "run_cancelled"
    assert MultiAgentEventKind.RUN_BUDGET_EXCEEDED.value == "run_budget_exceeded"
