"""Workflow Builder API — generic workflow CRUD / publish / execution.

Route prefix decision (must not collide with the fixed PDF flow): the
``/api/v1/workflows`` prefix is owned by ``app/api/workflows.py`` whose
``GET /{thread_id}`` would shadow a builder ``GET /{id}`` (FastAPI matches
in registration order). This router therefore uses the independent prefix
``/api/v1/workflow-builder`` and leaves the PDF router untouched.
"""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.audit.service import AuditActor
from app.auth.identity import IdentityContext
from app.auth.models import APIKey
from app.core.container import provide_workflow_builder_service
from app.ratelimit.dependencies import require_rate_limit
from app.workflow_builder.models import WorkflowRecord, WorkflowRunRecord
from app.workflow_builder.service import WorkflowBuilderService

router = APIRouter(prefix="/api/v1/workflow-builder", tags=["workflow-builder"])

_NOT_FOUND_MESSAGE = "Workflow not found."


class CreateWorkflowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2048)
    definition: dict[str, Any]


class UpdateWorkflowRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=2048)
    definition: dict[str, Any] | None = None


class RunWorkflowRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)


class WorkflowResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: str
    status: str
    version: int
    definition: dict[str, Any]
    created_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class WorkflowRunResponse(BaseModel):
    id: str
    workflow_id: str
    workspace_id: str
    status: str
    inputs: dict[str, Any]
    definition: dict[str, Any]
    node_results: list[dict[str, Any]]
    error: str | None = None
    total_duration_ms: int | None = None
    created_at: str | None = None
    completed_at: str | None = None


def _identity(request: Request) -> IdentityContext:
    context = getattr(request.state, "context", None)
    identity = cast(IdentityContext | None, context.identity if context else None)
    if identity is None or identity.workspace_id is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_MESSAGE)
    return identity


def _workspace_id(request: Request) -> str:
    return cast(str, _identity(request).workspace_id)


def _actor(request: Request) -> AuditActor:
    identity = _identity(request)
    return AuditActor(
        workspace_id=identity.workspace_id,
        api_key_hash=identity.api_key_hash,
        user_id=identity.user_id,
        ip=request.client.host if request.client else None,
    )


def _to_workflow_response(record: WorkflowRecord) -> WorkflowResponse:
    return WorkflowResponse(
        id=record.id,
        workspace_id=record.workspace_id,
        name=record.name,
        description=record.description,
        status=record.status,
        version=record.version,
        definition=dict(record.definition),
        created_by=record.created_by,
        created_at=record.created_at.isoformat() if record.created_at else None,
        updated_at=record.updated_at.isoformat() if record.updated_at else None,
    )


def _to_run_response(record: WorkflowRunRecord) -> WorkflowRunResponse:
    return WorkflowRunResponse(
        id=record.id,
        workflow_id=record.workflow_id,
        workspace_id=record.workspace_id,
        status=record.status,
        inputs=dict(record.inputs),
        definition=dict(record.definition),
        node_results=list(record.node_results),
        error=record.error,
        total_duration_ms=record.total_duration_ms,
        created_at=record.created_at.isoformat() if record.created_at else None,
        completed_at=record.completed_at.isoformat() if record.completed_at else None,
    )


# NOTE: ``/workflows/runs/{run_id}`` is registered before ``/workflows/{id}``
# so FastAPI matches the literal ``runs`` segment first.
@router.get("/workflows/runs/{run_id}", response_model=WorkflowRunResponse)
async def get_workflow_run(
    run_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[
        WorkflowBuilderService, Depends(provide_workflow_builder_service)
    ],
) -> WorkflowRunResponse:
    record = await service.get_run(run_id, _workspace_id(request))
    if record is None:
        raise HTTPException(status_code=404, detail="Workflow run not found.")
    return _to_run_response(record)


@router.post(
    "/workflows",
    response_model=WorkflowResponse,
    status_code=201,
)
async def create_workflow(
    body: CreateWorkflowRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[
        WorkflowBuilderService, Depends(provide_workflow_builder_service)
    ],
) -> WorkflowResponse:
    identity = _identity(request)
    record = await service.create_workflow(
        workspace_id=_workspace_id(request),
        name=body.name,
        definition=body.definition,
        description=body.description,
        created_by=identity.user_id,
    )
    return _to_workflow_response(record)


@router.get("/workflows", response_model=list[WorkflowResponse])
async def list_workflows(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[
        WorkflowBuilderService, Depends(provide_workflow_builder_service)
    ],
) -> list[WorkflowResponse]:
    records = await service.list_workflows(_workspace_id(request))
    return [_to_workflow_response(record) for record in records]


@router.get("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[
        WorkflowBuilderService, Depends(provide_workflow_builder_service)
    ],
) -> WorkflowResponse:
    record = await service.get_workflow(workflow_id, _workspace_id(request))
    if record is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_MESSAGE)
    return _to_workflow_response(record)


@router.put("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: str,
    body: UpdateWorkflowRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[
        WorkflowBuilderService, Depends(provide_workflow_builder_service)
    ],
) -> WorkflowResponse:
    if body.name is None and body.description is None and body.definition is None:
        raise HTTPException(status_code=422, detail="至少提供一个可更新的字段。")
    record = await service.update_workflow(
        workflow_id,
        _workspace_id(request),
        name=body.name,
        description=body.description,
        definition=body.definition,
    )
    if record is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_MESSAGE)
    return _to_workflow_response(record)


@router.post("/workflows/{workflow_id}/publish", response_model=WorkflowResponse)
async def publish_workflow(
    workflow_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[
        WorkflowBuilderService, Depends(provide_workflow_builder_service)
    ],
) -> WorkflowResponse:
    record = await service.publish_workflow(
        workflow_id, _workspace_id(request), actor=_actor(request)
    )
    if record is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_MESSAGE)
    return _to_workflow_response(record)


@router.post("/workflows/{workflow_id}/unpublish", response_model=WorkflowResponse)
async def unpublish_workflow(
    workflow_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[
        WorkflowBuilderService, Depends(provide_workflow_builder_service)
    ],
) -> WorkflowResponse:
    record = await service.unpublish_workflow(workflow_id, _workspace_id(request))
    if record is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_MESSAGE)
    return _to_workflow_response(record)


@router.post(
    "/workflows/{workflow_id}/runs",
    response_model=WorkflowRunResponse,
    status_code=201,
)
async def run_workflow(
    workflow_id: str,
    body: RunWorkflowRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[
        WorkflowBuilderService, Depends(provide_workflow_builder_service)
    ],
) -> WorkflowRunResponse:
    record = await service.run_workflow(
        workflow_id,
        _workspace_id(request),
        body.inputs,
        actor=_actor(request),
        request_context=request.state.context,
        api_key=_api_key,
    )
    if record is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_MESSAGE)
    return _to_run_response(record)


@router.get(
    "/workflows/{workflow_id}/runs",
    response_model=list[WorkflowRunResponse],
)
async def list_workflow_runs(
    workflow_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[
        WorkflowBuilderService, Depends(provide_workflow_builder_service)
    ],
    limit: int = Query(50, ge=1, le=200),
) -> list[WorkflowRunResponse]:
    records = await service.list_runs(workflow_id, _workspace_id(request), limit=limit)
    if not records:
        # Distinguish "no runs yet" from "workflow not found / not owned".
        workflow = await service.get_workflow(workflow_id, _workspace_id(request))
        if workflow is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND_MESSAGE)
    return [_to_run_response(record) for record in records]


@router.delete("/workflows/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[
        WorkflowBuilderService, Depends(provide_workflow_builder_service)
    ],
) -> None:
    deleted = await service.delete_workflow(workflow_id, _workspace_id(request))
    if not deleted:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_MESSAGE)
