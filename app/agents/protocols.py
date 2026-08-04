from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.agents.models import AgentDecision, AgentState


@dataclass(frozen=True)
class ToolContext:
    """Context supplied to a tool without coupling it to FastAPI or storage."""

    run_id: str
    step_index: int


@runtime_checkable
class AgentModel(Protocol):
    """Async model decision boundary used by the runtime."""

    async def decide(self, state: AgentState) -> AgentDecision: ...


@runtime_checkable
class AgentTool(Protocol):
    """Minimal async tool contract for Sprint 8 test and domain tools."""

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> str: ...
