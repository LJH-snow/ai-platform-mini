from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.agents.models import RunStatus, StopReason
from app.schemas.chat import ChatMessage


class AgentRunRequest(BaseModel):
    """Validated input for one synchronous Agent Runtime execution."""

    message: str = Field(min_length=1, description="Latest user message.")
    model: str | None = Field(default=None, min_length=1)
    system_prompt: str | None = Field(default=None)
    history: list[ChatMessage] = Field(default_factory=list)
    max_steps: int = Field(default=5, ge=1, le=20)
    timeout_seconds: float = Field(default=30.0, gt=0, le=120.0)
    token_budget: int = Field(default=2048, gt=0, le=32768)


class AgentStepSummary(BaseModel):
    """Safe summary of one runtime step without prompt or tool payloads."""

    index: int = Field(ge=1)
    decision_kind: Literal["final_answer", "tool_call", "invalid"]
    tool_names: list[str] = Field(default_factory=list)
    tool_succeeded: bool | None = None


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
