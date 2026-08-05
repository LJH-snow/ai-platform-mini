from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Protocol, runtime_checkable

from app.agents.models import AgentAnswerChunk, AgentDecision, AgentState, ToolContext


@runtime_checkable
class AgentModel(Protocol):
    """Async model decision boundary used by the runtime."""

    async def decide(self, state: AgentState) -> AgentDecision: ...


@runtime_checkable
class StreamingAgentModel(Protocol):
    """Optional boundary for real provider answer deltas."""

    def stream_answer(self, state: AgentState) -> AsyncIterator[AgentAnswerChunk]: ...


@runtime_checkable
class AgentTool(Protocol):
    """Minimal async tool contract for Sprint 8 test and domain tools."""

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> str: ...
