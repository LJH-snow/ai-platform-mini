from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.agents.models import AgentEvent


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
        self._terminal_observed = False

    def observe(self, event: AgentEvent) -> None:
        if event.kind.value == "run_stopped":
            self._terminal_observed = True
        self._queue.put_nowait(event)

    def close(self) -> None:
        self._queue.put_nowait(AgentStreamClosed())

    @property
    def terminal_observed(self) -> bool:
        return self._terminal_observed

    def fail_setup(self) -> None:
        if not self._terminal_observed:
            self._queue.put_nowait(AgentStreamSetupError())
        self.close()

    async def receive(
        self,
    ) -> AgentEvent | AgentStreamClosed | AgentStreamSetupError:
        return await self._queue.get()
