from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from app.agents.models import AgentEvent, AgentEventKind, RunStatus, StopReason


@dataclass(frozen=True)
class AgentStreamClosed:
    """Sentinel used to close one in-memory event subscription."""


@dataclass(frozen=True)
class AgentStreamSetupError:
    """Terminal SSE error emitted when a Run cannot start."""

    error_code: str = "stream_setup_failed"


class AgentEventStream:
    """Non-blocking observer bridge from Runtime events to one SSE consumer."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[
            AgentEvent | AgentStreamClosed | AgentStreamSetupError
        ] = asyncio.Queue()
        self._run_started_observed = False
        self._terminal_observed = False
        self._run_id: str | None = None
        self._last_sequence: int | None = None

    def observe(self, event: AgentEvent) -> None:
        if event.kind is AgentEventKind.RUN_STARTED:
            self._run_started_observed = True
            self._run_id = event.run_id
        elif event.kind is AgentEventKind.RUN_STOPPED:
            self._terminal_observed = True
        self._last_sequence = (
            event.sequence
            if self._last_sequence is None
            else max(self._last_sequence, event.sequence)
        )
        self._queue.put_nowait(event)

    def close(self) -> None:
        self._queue.put_nowait(AgentStreamClosed())

    @property
    def run_started_observed(self) -> bool:
        return self._run_started_observed

    @property
    def terminal_observed(self) -> bool:
        return self._terminal_observed

    def fail_setup(self) -> None:
        if not self._terminal_observed:
            self._queue.put_nowait(AgentStreamSetupError())
        self.close()

    def fail_unexpected(self) -> None:
        """Convert an unexpected producer failure to the correct stream terminal."""
        if self._terminal_observed:
            self.close()
            return
        if not self._run_started_observed:
            self.fail_setup()
            return

        if self._run_id is None:
            raise RuntimeError("run_started event did not include a run id")

        sequence = 1 if self._last_sequence is None else self._last_sequence + 1
        self.observe(
            AgentEvent(
                kind=AgentEventKind.RUN_STOPPED,
                run_id=self._run_id,
                sequence=sequence,
                occurred_at=datetime.now(UTC),
                status=RunStatus.FAILED,
                stop_reason=StopReason.MODEL_ERROR,
            )
        )
        self.close()

    async def receive(
        self,
    ) -> AgentEvent | AgentStreamClosed | AgentStreamSetupError:
        return await self._queue.get()
