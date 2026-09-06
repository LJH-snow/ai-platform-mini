"""Multi-agent orchestration API endpoints.

Exposes a synchronous run, an SSE stream of real lifecycle events, and a
tenant-scoped run history.  Every endpoint that consumes the API key applies
rate limiting and persists safe public projections of each run.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.models import APIKey
from app.core.context import RequestContext
from app.evals.multi_agent_compare import MultiAgentCompareRunner
from app.multi_agent.events import (
    MultiAgentEvent,
    MultiAgentEventKind,
    _bounded_result,
)
from app.multi_agent.models import (
    OrchestrationConfig,
    OrchestrationResult,
    SubtaskResult,
)
from app.multi_agent.service import MultiAgentService
from app.ratelimit.dependencies import require_rate_limit
from app.schemas.agent import MAX_AGENT_MAX_STEPS
from app.schemas.multi_agent import (
    MultiAgentRunDetail,
    MultiAgentRunRequest,
    MultiAgentRunResponse,
    MultiAgentRunSummary,
    MultiAgentStreamEvent,
    SubtaskResultResponse,
    SubtaskResultSummarySchema,
    SubtaskSummarySchema,
)
from app.services.multi_agent_run_record_service import (
    MultiAgentRunRecordService,
    project_run_response,
    public_run_payload,
    public_run_summary,
)

router = APIRouter(prefix="/api/v1/multi-agent", tags=["multi-agent"])
logger = logging.getLogger(__name__)

_MAX_SUMMARY_CHARS = 256
_MAX_RESULT_CHARS = 8192


class MultiAgentBenchmarkRunRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)
    max_steps: int | None = Field(
        default=None,
        ge=1,
        le=MAX_AGENT_MAX_STEPS,
    )


class MultiAgentBenchmarkRunResponse(BaseModel):
    id: int
    agent_id: str
    workspace_id: str
    task_set: str
    tool_call_accuracy: float | None = None
    task_completion_rate: float | None = None
    task_count: int = 0
    completed_count: int = 0
    created_at: datetime | None = None
    metric_payload: dict[str, object] = Field(default_factory=dict)


_SAFE_ERROR_MESSAGES: dict[str, str] = {
    "supervisor_failed": "Supervisor decomposition failed.",
    "dependency_deadlock": "Dependency deadlock detected.",
    "timeout": "Multi-agent run timed out.",
    "cancelled": "Multi-agent run was cancelled.",
    "budget_exceeded": "Token budget exceeded.",
    "subtask_failed": "Subtask failed.",
    "internal_error": "Multi-agent run failed.",
    "stream_setup_failed": "Stream setup failed.",
}


def _public_error_text(error_code: str | None) -> str | None:
    """Map a public error code to a safe message; never returns raw internals."""
    if error_code is None:
        return None
    return _SAFE_ERROR_MESSAGES.get(error_code, _SAFE_ERROR_MESSAGES["internal_error"])


def _subtask_public_error(result: SubtaskResult) -> str | None:
    """Return a safe subtask error message without raw exception text."""
    if result.status.value != "failed":
        return None
    return _SAFE_ERROR_MESSAGES["subtask_failed"]


# ── Dependency injection ────────────────────────────────────────────────────


def _provide_multi_agent_service() -> MultiAgentService:
    """Provide MultiAgentService instance via container."""
    from app.core.container import provide_agent_service, provide_chat_service

    chat_service = provide_chat_service()
    return MultiAgentService(chat_service, agent_service=provide_agent_service())


def _provide_multi_agent_record_service() -> MultiAgentRunRecordService | None:
    """Provide the run-record service; None when no engine is configured."""
    from app.core.container import provide_multi_agent_run_record_service

    return provide_multi_agent_run_record_service()


def _provide_multi_agent_compare_runner() -> MultiAgentCompareRunner:
    """Provide the M4 single vs multi-agent comparison runner."""
    from app.core.container import provide_multi_agent_compare_runner

    return provide_multi_agent_compare_runner()


def _owner_scope(request: Request) -> str:
    """Resolve the run-record tenant scope for the authenticated identity.

    Run records store the raw workspace id (or NULL for legacy keys), so
    the scope is the workspace id itself, falling back to the key hash.
    """
    context: RequestContext = request.state.context
    identity = context.identity
    if identity is None:
        raise HTTPException(status_code=401, detail="Identity not resolved.")
    if identity.workspace_id is not None:
        return identity.workspace_id
    return identity.api_key_hash


def _benchmark_record_to_response(
    record: object,
) -> MultiAgentBenchmarkRunResponse:
    """Convert one persisted benchmark record to the public response."""
    from app.evals.benchmark_repository import BenchmarkRunRecord

    assert isinstance(record, BenchmarkRunRecord)
    return MultiAgentBenchmarkRunResponse(
        id=record.id,
        agent_id=record.agent_id,
        workspace_id=record.workspace_id,
        task_set=record.task_set,
        tool_call_accuracy=record.tool_call_accuracy,
        task_completion_rate=record.task_completion_rate,
        task_count=record.task_count,
        completed_count=record.completed_count,
        created_at=record.created_at,
        metric_payload=dict(record.metric_payload),
    )


# ── Stream bridge ───────────────────────────────────────────────────────────


class MultiAgentStreamClosed:
    """Sentinel used to close one in-memory event subscription."""


class MultiAgentStreamSetupError:
    """Terminal SSE error emitted when a Run cannot start."""

    error_code: str = "stream_setup_failed"


class MultiAgentEventStream:
    """Non-blocking observer bridge from service events to one SSE consumer.

    Sequence numbers are already assigned by the service's SequencedObserver,
    so this bridge only forwards real events and tracks terminal/start state to
    decide what a producer failure should emit.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[
            MultiAgentEvent | MultiAgentStreamClosed | MultiAgentStreamSetupError
        ] = asyncio.Queue()
        self._terminal_observed = False
        self._run_id: str | None = None
        self._last_sequence: int | None = None

    async def on_event(self, event: MultiAgentEvent) -> None:
        if event.is_terminal:
            self._terminal_observed = True
        if self._run_id is None:
            self._run_id = event.run_id
        self._last_sequence = (
            event.sequence
            if self._last_sequence is None
            else max(self._last_sequence, event.sequence)
        )
        self._queue.put_nowait(event)

    def close(self) -> None:
        self._queue.put_nowait(MultiAgentStreamClosed())

    @property
    def terminal_observed(self) -> bool:
        return self._terminal_observed

    def fail_setup(self) -> None:
        if not self._terminal_observed:
            self._queue.put_nowait(MultiAgentStreamSetupError())
        self.close()

    def fail_unexpected(self) -> None:
        """Convert an unexpected producer failure to a truthful terminal."""
        if self._terminal_observed:
            self.close()
            return
        if self._run_id is None:
            self.fail_setup()
            return
        from datetime import UTC, datetime

        self._terminal_observed = True
        self._queue.put_nowait(
            MultiAgentEvent(
                run_id=self._run_id,
                kind=MultiAgentEventKind.RUN_FAILED,
                sequence=(
                    1 if self._last_sequence is None else self._last_sequence + 1
                ),
                occurred_at=datetime.now(UTC),
                error_code="internal_error",
            )
        )
        self.close()

    async def receive(
        self,
    ) -> MultiAgentEvent | MultiAgentStreamClosed | MultiAgentStreamSetupError:
        return await self._queue.get()


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
    response: Response,
    service: Annotated[MultiAgentService, Depends(_provide_multi_agent_service)],
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    record_service: Annotated[
        MultiAgentRunRecordService | None, Depends(_provide_multi_agent_record_service)
    ] = None,
) -> MultiAgentRunResponse:
    """Execute one synchronous multi-agent run through the application service."""
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
        context=context,
        api_key=api_key,
    )
    result_error_code = result.error_code
    public_response = MultiAgentRunResponse(
        run_id=result.run_id,
        status=result.status.value,
        final_output=(
            _bounded_result(result.final_output) if result.final_output else ""
        ),
        subtask_results=[
            SubtaskResultResponse(
                task_id=r.task_id,
                status=r.status.value,
                output=r.output,
                error=_subtask_public_error(r),
                error_code=("subtask_failed" if r.status.value == "failed" else None),
                agent_role=r.agent_role.value,
                token_usage=r.token_usage,
                steps_taken=r.steps_taken,
                duration_ms=r.duration_ms,
            )
            for r in result.subtask_results
        ],
        total_token_usage=result.total_token_usage,
        error=_public_error_text(result_error_code),
        error_code=result_error_code,
        duration_ms=result.duration_ms,
    )
    await _persist_run(record_service, public_response, context, api_key, result)
    _set_rate_limit_headers(request, response)
    return public_response


@router.post(
    "/runs/stream",
    response_model=None,
    summary="Stream a multi-agent run",
    description="Stream real lifecycle events as server-sent events.",
)
async def stream_multi_agent_run(
    body: MultiAgentRunRequest,
    request: Request,
    service: Annotated[MultiAgentService, Depends(_provide_multi_agent_service)],
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    record_service: Annotated[
        MultiAgentRunRecordService | None, Depends(_provide_multi_agent_record_service)
    ] = None,
) -> StreamingResponse:
    """Stream real multi-agent lifecycle events to the client."""
    context: RequestContext = request.state.context
    stream = MultiAgentEventStream()
    cancel_event = asyncio.Event()

    async def produce() -> None:
        try:
            result = await service.run(
                user_input=body.message,
                config=OrchestrationConfig(
                    max_concurrency=body.max_concurrency,
                    failure_policy=body.failure_policy,
                    total_timeout=body.total_timeout,
                    total_token_budget=body.total_token_budget,
                    supervisor_model=body.supervisor_model,
                ),
                request_id=context.request_id,
                supervisor_model=body.supervisor_model,
                max_subtasks=body.max_subtasks,
                observer=stream,
                cancel_event=cancel_event,
                context=context,
                api_key=api_key,
            )
            result_error_code = result.error_code
            public_response = MultiAgentRunResponse(
                run_id=result.run_id,
                status=result.status.value,
                final_output=(
                    _bounded_result(result.final_output) if result.final_output else ""
                ),
                subtask_results=[
                    SubtaskResultResponse(
                        task_id=r.task_id,
                        status=r.status.value,
                        output=r.output,
                        error=_subtask_public_error(r),
                        error_code=(
                            "subtask_failed" if r.status.value == "failed" else None
                        ),
                        agent_role=r.agent_role.value,
                        token_usage=r.token_usage,
                        steps_taken=r.steps_taken,
                        duration_ms=r.duration_ms,
                    )
                    for r in result.subtask_results
                ],
                total_token_usage=result.total_token_usage,
                error=_public_error_text(result_error_code),
                error_code=result_error_code,
                duration_ms=result.duration_ms,
            )
            await _persist_run(
                record_service, public_response, context, api_key, result
            )
        except Exception:
            stream.fail_unexpected()
            return
        finally:
            if not stream.terminal_observed:
                stream.close()

    task = asyncio.create_task(produce())

    remaining = getattr(request.state, "rate_limit_remaining", None)
    limit = getattr(request.state, "rate_limit_limit", None)
    reset_after = getattr(request.state, "rate_limit_reset_after", None)
    rate_headers: dict[str, str] = {}
    if remaining is not None and limit is not None:
        rate_headers["X-RateLimit-Limit"] = str(limit)
        rate_headers["X-RateLimit-Remaining"] = str(remaining)
        if reset_after is not None:
            rate_headers["X-RateLimit-Reset"] = str(reset_after)

    return StreamingResponse(
        _stream_events(request, stream, context.request_id, task, cancel_event),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            **rate_headers,
        },
    )


@router.get(
    "/runs",
    response_model=list[MultiAgentRunSummary],
    summary="List multi-agent run history",
)
async def list_multi_agent_runs(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    record_service: Annotated[
        MultiAgentRunRecordService | None, Depends(_provide_multi_agent_record_service)
    ] = None,
    limit: int = 50,
) -> list[MultiAgentRunSummary]:
    """List this tenant's run history (workspace- or key-scoped)."""
    if record_service is None:
        raise HTTPException(status_code=503, detail="Run history unavailable")
    owner_scope = _owner_scope(request)
    rows = await record_service.list_runs(
        limit=min(max(limit, 1), 200), owner_scope=owner_scope
    )
    return [
        MultiAgentRunSummary(
            **public_run_summary(public_run_payload(row))  # type: ignore[arg-type]
        )
        for row in rows
    ]


@router.get(
    "/runs/{run_id}",
    response_model=MultiAgentRunDetail,
    summary="Get one multi-agent run detail",
)
async def get_multi_agent_run(
    run_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    record_service: Annotated[
        MultiAgentRunRecordService | None, Depends(_provide_multi_agent_record_service)
    ] = None,
) -> MultiAgentRunDetail:
    """Fetch one run; cross-tenant reads return 404."""
    if record_service is None:
        raise HTTPException(status_code=503, detail="Run history unavailable")
    owner_scope = _owner_scope(request)
    row = await record_service.get_run(run_id, owner_scope=owner_scope)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    payload = public_run_payload(row)
    detail_fields = {key: value for key, value in payload.items() if key != "response"}
    return MultiAgentRunDetail(
        **detail_fields,  # type: ignore[arg-type]
        response=project_run_response(payload),
    )


@router.post(
    "/benchmark",
    response_model=MultiAgentBenchmarkRunResponse,
    summary="Run single-agent vs multi-agent comparison",
    status_code=201,
)
async def run_multi_agent_benchmark(
    body: MultiAgentBenchmarkRunRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    runner: Annotated[
        MultiAgentCompareRunner, Depends(_provide_multi_agent_compare_runner)
    ],
) -> MultiAgentBenchmarkRunResponse:
    """Run the M4 golden comparison set and return persisted metrics."""
    identity = request.state.context.identity
    workspace_id = identity.workspace_id if identity else None
    if workspace_id is None:
        raise HTTPException(
            status_code=404, detail="Agent not found or not accessible."
        )
    try:
        record = await runner.run(
            body.agent_id,
            workspace_id=workspace_id,
            context=request.state.context,
            api_key=_api_key,
            max_steps=body.max_steps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _benchmark_record_to_response(record)


@router.get(
    "/benchmark/runs",
    response_model=list[MultiAgentBenchmarkRunResponse],
    summary="List multi-agent comparison benchmark runs",
)
async def list_multi_agent_benchmark_runs(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    runner: Annotated[
        MultiAgentCompareRunner, Depends(_provide_multi_agent_compare_runner)
    ],
    agent_id: str | None = None,
) -> list[MultiAgentBenchmarkRunResponse]:
    identity = request.state.context.identity
    workspace_id = identity.workspace_id if identity else None
    if workspace_id is None:
        return []
    records = await runner.list_runs(workspace_id, agent_id=agent_id)
    return [_benchmark_record_to_response(record) for record in records]


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _persist_run(
    record_service: MultiAgentRunRecordService | None,
    response: MultiAgentRunResponse,
    context: RequestContext,
    api_key: APIKey,
    result: object,
) -> None:
    """Persist one run; failures must never break the response/stream."""
    if record_service is None:
        return
    try:
        if not isinstance(result, OrchestrationResult):
            return
        await record_service.save(
            result,
            context=context,
            api_key=api_key,
            request_id=context.request_id,
            status=result.status,
            final_output=(
                _bounded_result(result.final_output) if result.final_output else ""
            ),
            error_code=result.error_code,
            started_at=result.started_at,
        )
    except Exception:
        logger.exception("Failed to persist multi-agent Run run_id=%s", response.run_id)


async def _stream_events(
    http_request: Request,
    stream: MultiAgentEventStream,
    request_id: str,
    producer: asyncio.Task[None],
    cancel_event: asyncio.Event,
) -> AsyncIterator[str]:
    """Consume real events and poll disconnect without blocking the queue."""
    receive_task = asyncio.create_task(stream.receive())
    try:
        while True:
            disconnect_task = asyncio.create_task(http_request.is_disconnected())
            poll_task = asyncio.create_task(asyncio.sleep(0.1))
            done, _ = await asyncio.wait(
                (receive_task, disconnect_task, poll_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for pending in (disconnect_task, poll_task):
                if pending not in done:
                    pending.cancel()
            await asyncio.gather(disconnect_task, poll_task, return_exceptions=True)

            if disconnect_task in done and disconnect_task.result():
                cancel_event.set()
                break
            if receive_task not in done:
                continue

            item = receive_task.result()
            if isinstance(item, MultiAgentStreamClosed):
                break
            if isinstance(item, MultiAgentStreamSetupError):
                payload = json.dumps(
                    {
                        "event": "stream_error",
                        "error_code": item.error_code,
                        "request_id": request_id,
                    },
                    separators=(",", ":"),
                )
                yield f"event: stream_error\ndata: {payload}\n\n"
                break
            yield _serialize_sse(_to_stream_event(item, request_id))
            if item.is_terminal:
                break
            receive_task = asyncio.create_task(stream.receive())
    finally:
        if not receive_task.done():
            receive_task.cancel()
        await asyncio.gather(receive_task, return_exceptions=True)
        if not producer.done():
            cancel_event.set()
        await asyncio.gather(producer, return_exceptions=True)


def _serialize_sse(event: MultiAgentStreamEvent) -> str:
    payload = event.model_dump_json(exclude_none=True)
    return f"event: {event.event}\ndata: {payload}\n\n"


def _to_stream_event(event: MultiAgentEvent, request_id: str) -> MultiAgentStreamEvent:
    return MultiAgentStreamEvent(
        event=event.kind.value,
        run_id=event.run_id,
        request_id=request_id,
        sequence=event.sequence,
        occurred_at=event.occurred_at,
        task_id=event.task_id,
        agent_role=event.agent_role,
        duration_ms=event.duration_ms,
        token_usage=event.token_usage,
        output_summary=event.output_summary,
        error_code=event.error_code,
        subtasks=[
            SubtaskSummarySchema(
                id=s.id,
                agent_role=s.agent_role,
                description=s.description,
                depends_on=list(s.depends_on),
            )
            for s in event.subtasks
        ],
        reasoning=event.reasoning,
        final_output=event.final_output,
        total_token_usage=event.total_token_usage,
        subtask_results=[
            SubtaskResultSummarySchema(
                task_id=r.task_id,
                status=r.status,
                agent_role=r.agent_role,
                output=r.output,
                error_code=r.error_code,
                token_usage=r.token_usage,
                duration_ms=r.duration_ms,
            )
            for r in event.subtask_results
        ],
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
