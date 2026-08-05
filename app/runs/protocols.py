from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.agents.models import AgentEvent, AgentRunResult
from app.runs.models import RunTrace, RunTraceEvent


@runtime_checkable
class AgentEventObserver(Protocol):
    """Synchronous observer boundary for runtime lifecycle events."""

    def observe(self, event: AgentEvent) -> None: ...


@runtime_checkable
class RunTraceSanitizer(Protocol):
    """Convert runtime events into privacy-safe trace events."""

    def sanitize(
        self,
        event: AgentEvent,
        started_at: datetime,
    ) -> RunTraceEvent: ...


@runtime_checkable
class RunTraceRecorderProtocol(AgentEventObserver, Protocol):
    """Recorder hooks for exactly one run per recorder instance.

    A runtime may execute concurrent runs, but each run must receive its own
    recorder instance. Use ``RunTraceRecorderFactory`` when constructing a
    shared runtime for concurrent requests.
    """

    def start(
        self,
        run_id: str,
        *,
        request_id: str | None,
        model: str | None,
        started_at: datetime,
    ) -> None: ...

    def finish(self, result: AgentRunResult) -> None: ...

    def snapshot(self) -> RunTrace | None: ...


@runtime_checkable
class RunTraceRecorderFactory(Protocol):
    """Create a fresh single-run recorder for each AgentRuntime.run call."""

    def __call__(self) -> RunTraceRecorderProtocol: ...
