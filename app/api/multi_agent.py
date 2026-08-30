"""Multi-agent orchestration API endpoints."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.context import RequestContext
from app.multi_agent.models import (
    FailurePolicy,
    OrchestrationConfig,
)
from app.multi_agent.service import MultiAgentService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/multi-agent", tags=["multi-agent"])


# ── Request / Response schemas ──────────────────────────────────────────────


class MultiAgentRunRequest(BaseModel):
    """Request body for a multi-agent run."""

    message: str = Field(
        ..., min_length=1, description="User task to decompose and execute"
    )
    supervisor_model: str | None = Field(
        None, description="Model for supervisor decomposition"
    )
    max_subtasks: int = Field(5, ge=1, le=10, description="Maximum number of subtasks")
    max_concurrency: int = Field(3, ge=1, le=10, description="Maximum parallel agents")
    failure_policy: FailurePolicy = Field(
        FailurePolicy.FAIL_FAST, description="How to handle failed subtasks"
    )
    total_timeout: float | None = Field(
        300.0, ge=1.0, description="Global timeout in seconds"
    )
    total_token_budget: int | None = Field(None, ge=1, description="Total token budget")


class SubtaskResultResponse(BaseModel):
    """Response for a single subtask result."""

    task_id: str
    status: str
    output: str = ""
    error: str | None = None
    agent_role: str
    token_usage: int = 0
    steps_taken: int = 0
    duration_ms: int | None = None


class MultiAgentRunResponse(BaseModel):
    """Response for a multi-agent run."""

    run_id: str
    status: str
    final_output: str = ""
    subtask_results: list[SubtaskResultResponse] = []
    total_token_usage: int = 0
    error: str | None = None
    duration_ms: int | None = None


# ── Dependency injection ────────────────────────────────────────────────────


def _provide_multi_agent_service() -> MultiAgentService:
    """Provide MultiAgentService instance via container."""
    from app.core.container import provide_chat_service

    chat_service = provide_chat_service()
    return MultiAgentService(chat_service)


# ── Routes ──────────────────────────────────────────────────────────────────


@router.post(
    "/runs",
    response_model=MultiAgentRunResponse,
    summary="Execute a multi-agent run",
    description="Decompose task via Supervisor, then execute with agents.",
)
async def create_multi_agent_run(
    body: MultiAgentRunRequest,
    request: Request,
    service: Annotated[MultiAgentService, Depends(_provide_multi_agent_service)],
) -> MultiAgentRunResponse:
    context: RequestContext = request.state.context
    config = OrchestrationConfig(
        max_concurrency=body.max_concurrency,
        failure_policy=body.failure_policy,
        total_timeout=body.total_timeout,
        total_token_budget=body.total_token_budget,
        supervisor_model=body.supervisor_model,
    )
    result = await service.run(
        user_input=body.message,
        config=config,
        request_id=context.request_id,
        supervisor_model=body.supervisor_model,
        max_subtasks=body.max_subtasks,
    )
    return MultiAgentRunResponse(
        run_id=result.run_id,
        status=result.status.value,
        final_output=result.final_output,
        subtask_results=[
            SubtaskResultResponse(
                task_id=r.task_id,
                status=r.status.value,
                output=r.output,
                error=r.error,
                agent_role=r.agent_role.value,
                token_usage=r.token_usage,
                steps_taken=r.steps_taken,
                duration_ms=r.duration_ms,
            )
            for r in result.subtask_results
        ],
        total_token_usage=result.total_token_usage,
        error=result.error,
        duration_ms=result.duration_ms,
    )
