"""Agent Benchmark API — golden task set evaluation routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.core.container import provide_agent_benchmark_runner
from app.evals.agent_benchmark import AgentBenchmarkRunner
from app.evals.benchmark_repository import BenchmarkRunRecord

router = APIRouter(prefix="/api/v1/benchmarks", tags=["benchmarks"])


class BenchmarkRunRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)
    task_set: str = Field(default="default", min_length=1, max_length=64)


class BenchmarkRunResponse(BaseModel):
    id: int
    agent_id: str
    workspace_id: str
    task_set: str
    tool_call_accuracy: float | None = None
    task_completion_rate: float | None = None
    average_steps: float | None = None
    average_latency_ms: float | None = None
    task_count: int = 0
    completed_count: int = 0
    created_at: datetime | None = None


def _to_response(record: BenchmarkRunRecord) -> BenchmarkRunResponse:
    return BenchmarkRunResponse(
        id=record.id,
        agent_id=record.agent_id,
        workspace_id=record.workspace_id,
        task_set=record.task_set,
        tool_call_accuracy=record.tool_call_accuracy,
        task_completion_rate=record.task_completion_rate,
        average_steps=record.average_steps,
        average_latency_ms=record.average_latency_ms,
        task_count=record.task_count,
        completed_count=record.completed_count,
        created_at=record.created_at,
    )


@router.post("/run", response_model=BenchmarkRunResponse, status_code=201)
async def run_benchmark(
    body: BenchmarkRunRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    runner: Annotated[AgentBenchmarkRunner, Depends(provide_agent_benchmark_runner)],
) -> BenchmarkRunResponse:
    identity = request.state.context.identity
    ws_id = identity.workspace_id if identity else None
    if ws_id is None:
        # Conservative tenant boundary: benchmarks execute a workspace's
        # agent, so keys without a workspace scope are rejected.
        raise HTTPException(
            status_code=404, detail="Agent not found or not accessible."
        )
    try:
        record = await runner.run(
            body.agent_id,
            body.task_set,
            workspace_id=ws_id,
            context=request.state.context,
            api_key=_api_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_response(record)


@router.get("/runs", response_model=list[BenchmarkRunResponse])
async def list_benchmark_runs(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    runner: Annotated[AgentBenchmarkRunner, Depends(provide_agent_benchmark_runner)],
    agent_id: str | None = Query(default=None, max_length=128),
) -> list[BenchmarkRunResponse]:
    identity = request.state.context.identity
    ws_id = identity.workspace_id if identity else None
    if ws_id is None:
        # Mirrors list_agents: unbound keys see no workspace data.
        return []
    records = await runner.list_runs(ws_id, agent_id=agent_id)
    return [_to_response(record) for record in records]
