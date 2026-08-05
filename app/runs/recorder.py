from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TextIO

from app.agents.models import (
    AgentEvent,
    AgentRunResult,
    RunStatus,
    StopReason,
)
from app.runs.models import RunTrace, RunTraceEvent
from app.runs.protocols import RunTraceRecorderProtocol, RunTraceSanitizer
from app.runs.sanitizer import DefaultRunTraceSanitizer

logger = logging.getLogger(__name__)


class InMemoryRunTraceRecorder(RunTraceRecorderProtocol):
    """Store one sanitized trace; create one instance per agent run."""

    def __init__(self, sanitizer: RunTraceSanitizer | None = None) -> None:
        self._sanitizer = sanitizer or DefaultRunTraceSanitizer()
        self._events: list[RunTraceEvent] = []
        self._run_id: str | None = None
        self._request_id: str | None = None
        self._model: str | None = None
        self._started_at: datetime | None = None
        self._recorder_errors: list[str] = []

    def start(
        self,
        run_id: str,
        *,
        request_id: str | None,
        model: str | None,
        started_at: datetime,
    ) -> None:
        """Initialize metadata once and reject reuse for another run."""

        if self._run_id is not None:
            if self._run_id != run_id:
                raise ValueError(
                    "InMemoryRunTraceRecorder is single-run and cannot be reused"
                )
            return
        self._run_id = run_id
        self._request_id = request_id
        self._model = model
        self._started_at = started_at

    def observe(self, event: AgentEvent) -> None:
        """Record one event while keeping recorder failures non-fatal."""

        try:
            if self._started_at is None:
                self.start(
                    event.run_id,
                    request_id=None,
                    model=None,
                    started_at=event.occurred_at,
                )
            if self._run_id != event.run_id or self._started_at is None:
                raise ValueError("trace event belongs to a different run")
            self._events.append(self._sanitizer.sanitize(event, self._started_at))
        except Exception:
            self._recorder_errors.append("trace_observation_failed")
            logger.exception("Run trace observation failed")

    def finish(self, result: AgentRunResult) -> None:
        """Accept the terminal result without retaining raw runtime payloads."""

        if self._run_id is None:
            self._run_id = result.run_id
        elif self._run_id != result.run_id:
            raise ValueError(
                "InMemoryRunTraceRecorder is single-run and cannot be reused"
            )

    def snapshot(self) -> RunTrace | None:
        """Return the trace collected so far, if at least one event was observed."""

        if not self._events or self._started_at is None or self._run_id is None:
            return None
        terminal = next(
            (event for event in reversed(self._events) if event.kind == "run_stopped"),
            None,
        )
        status = _status_from_event(terminal)
        stop_reason = _stop_reason_from_event(terminal)
        completed_at = None if terminal is None else terminal.occurred_at
        token_usage = next(
            (
                event.cumulative_token_usage
                for event in reversed(self._events)
                if event.cumulative_token_usage is not None
            ),
            None,
        )
        return RunTrace(
            run_id=self._run_id,
            request_id=self._request_id,
            model=self._model,
            started_at=self._started_at,
            completed_at=completed_at,
            status=status,
            stop_reason=stop_reason,
            token_usage=token_usage,
            events=tuple(self._events),
            recorder_errors=tuple(self._recorder_errors),
        )

    @property
    def trace(self) -> RunTrace | None:
        """Expose the current snapshot for simple integrations."""

        return self.snapshot()

    def to_jsonl(self) -> str:
        """Serialize the current trace as JSONL."""

        trace = self.snapshot()
        return "" if trace is None else trace.to_jsonl()

    def write_jsonl(self, stream: TextIO) -> None:
        """Write the current trace to a text stream."""

        stream.write(self.to_jsonl())

    def write_jsonl_file(self, path: str | Path) -> None:
        """Write the current trace to a UTF-8 JSONL file."""

        Path(path).write_text(self.to_jsonl(), encoding="utf-8")


RunTraceRecorder = InMemoryRunTraceRecorder


def read_jsonl(source: str | TextIO | Path) -> RunTrace:
    """Read a JSONL trace from text or a UTF-8 file."""

    if isinstance(source, Path):
        payload = source.read_text(encoding="utf-8")
    elif isinstance(source, str):
        payload = source
    else:
        payload = source.read()
    return RunTrace.from_jsonl(payload)


def write_jsonl(trace: RunTrace, destination: TextIO | Path) -> None:
    """Write a trace to a text stream or UTF-8 file."""

    payload = trace.to_jsonl()
    if isinstance(destination, Path):
        destination.write_text(payload, encoding="utf-8")
    else:
        destination.write(payload)


def _status_from_event(event: RunTraceEvent | None) -> RunStatus | None:
    if event is None or event.status is None:
        return None
    try:
        return RunStatus(event.status)
    except ValueError:
        return None


def _stop_reason_from_event(event: RunTraceEvent | None) -> StopReason | None:
    if event is None or event.stop_reason is None:
        return None
    try:
        return StopReason(event.stop_reason)
    except ValueError:
        return None
