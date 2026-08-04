from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response

from app.auth.models import APIKey
from app.core.context import RequestContext
from app.ratelimit.dependencies import require_rate_limit
from app.schemas.agent import (
    AgentEventSummary,
    AgentRunRequest,
    AgentRunResponse,
    AgentStepSummary,
    AgentUsage,
)
from app.services.agent_service import AgentRunOutcome, AgentService, get_agent_service

router = APIRouter(prefix="/api/v1", tags=["agent"])


@router.post(
    "/agent/runs",
    response_model=AgentRunResponse,
    summary="Run a bounded Agent Runtime execution",
)
async def create_agent_run(
    request: AgentRunRequest,
    http_request: Request,
    response: Response,
    service: Annotated[AgentService, Depends(get_agent_service)],
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
) -> AgentRunResponse:
    """Execute one synchronous Agent Run through the application service."""
    context: RequestContext = http_request.state.context
    outcome = await service.run(request, context=context, api_key=api_key)
    _set_rate_limit_headers(http_request, response)
    return _to_response(outcome)


def _to_response(outcome: AgentRunOutcome) -> AgentRunResponse:
    result = outcome.result
    total_tokens: int | None = None
    if outcome.prompt_tokens is not None and outcome.completion_tokens is not None:
        total_tokens = outcome.prompt_tokens + outcome.completion_tokens
    return AgentRunResponse(
        run_id=result.run_id,
        status=result.status,
        answer=result.answer,
        stop_reason=result.stop_reason,
        steps=[_to_step_summary(step) for step in result.state.steps],
        events=[
            AgentEventSummary(
                kind=event.kind.value,
                step_index=event.step_index,
                status=event.status,
                stop_reason=event.stop_reason,
            )
            for event in result.events
        ],
        usage=AgentUsage(
            prompt_tokens=outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens,
            total_tokens=total_tokens,
            estimated=outcome.estimated_usage,
        ),
    )


def _to_step_summary(step: object) -> AgentStepSummary:
    from app.agents.models import AgentStep

    if not isinstance(step, AgentStep):
        raise TypeError("Agent Runtime returned an invalid step")

    if step.decision.answer is not None:
        decision_kind: Literal["final_answer", "tool_call", "invalid"] = "final_answer"
    elif step.decision.tool_calls:
        decision_kind = "tool_call"
    else:
        decision_kind = "invalid"

    succeeded_values = [result.succeeded for result in step.tool_results]
    tool_succeeded: bool | None = None
    if succeeded_values:
        tool_succeeded = all(succeeded_values)

    return AgentStepSummary(
        index=step.index,
        decision_kind=decision_kind,
        tool_names=[call.name for call in step.decision.tool_calls],
        tool_succeeded=tool_succeeded,
    )


def _set_rate_limit_headers(http_request: Request, response: Response) -> None:
    remaining = getattr(http_request.state, "rate_limit_remaining", None)
    limit = getattr(http_request.state, "rate_limit_limit", None)
    reset_after = getattr(http_request.state, "rate_limit_reset_after", None)
    if remaining is None or limit is None:
        return
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    if reset_after is not None:
        response.headers["X-RateLimit-Reset"] = str(reset_after)
