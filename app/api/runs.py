"""User-facing Agent Run replay endpoints (Sprint D1)."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth.identity import IdentityContext
from app.auth.models import APIKey
from app.core.container import provide_agent_run_record_service
from app.ratelimit.dependencies import require_rate_limit
from app.schemas.admin import AgentRunRecordResponse, AgentRunRecordSummary
from app.services.agent_run_record_service import (
    AgentRunRecordService,
    project_run_response,
    public_run_payload,
    public_run_summary,
)

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def _owner_scope(request: Request) -> str:
    """Resolve the run-record tenant scope for the authenticated identity.

    Run records store the raw workspace id (or NULL for legacy keys), so
    the scope is the workspace id itself, falling back to the key hash.
    """
    identity = cast(IdentityContext | None, request.state.context.identity)
    if identity is None:
        raise HTTPException(status_code=401, detail="Identity not resolved.")
    workspace_id = identity.workspace_id
    if workspace_id is not None:
        return workspace_id
    return identity.api_key_hash


def _require_service(
    record_service: AgentRunRecordService | None,
) -> AgentRunRecordService:
    if record_service is None:
        raise HTTPException(
            status_code=503, detail="Agent Run records are unavailable."
        )
    return record_service


@router.get("", response_model=list[AgentRunRecordSummary])
async def list_my_runs(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    record_service: Annotated[
        AgentRunRecordService | None, Depends(provide_agent_run_record_service)
    ],
    limit: int = Query(50, ge=1, le=200),
    agent_id: str | None = Query(None, max_length=128),
) -> list[AgentRunRecordSummary]:
    """List the authenticated tenant's own Agent runs (Replay navigation)."""
    service = _require_service(record_service)
    scope = _owner_scope(request)
    rows = await service.list_runs(limit=limit, owner_scope=scope, agent_id=agent_id)
    return [public_run_summary(public_run_payload(row)) for row in rows]


@router.get("/{run_id}", response_model=AgentRunRecordResponse)
async def get_my_run(
    run_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    record_service: Annotated[
        AgentRunRecordService | None, Depends(provide_agent_run_record_service)
    ],
) -> AgentRunRecordResponse:
    """Fetch one of the authenticated tenant's own runs (cross-tenant 404)."""
    service = _require_service(record_service)
    scope = _owner_scope(request)
    row = await service.get_run(run_id, owner_scope=scope)
    if row is None:
        raise HTTPException(status_code=404, detail="Agent Run record not found.")
    return project_run_response(public_run_payload(row))
