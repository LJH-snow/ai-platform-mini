"""HTTP API for the stateful LangGraph PDF report workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel

from app.auth.models import APIKey
from app.auth.tenant import resolve_tenant_scope
from app.core.container import provide_workflow_service
from app.core.settings import get_settings
from app.exceptions.base import RAGDocumentTooLargeError, RAGUnavailableError
from app.ratelimit.dependencies import require_rate_limit
from app.schemas.workflow import WorkflowRejectRequest, WorkflowStatusResponse
from app.services.workflow_service import PDFReportWorkflowService

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


def get_workflow_service() -> PDFReportWorkflowService:
    service = provide_workflow_service()
    if service is None:
        raise RAGUnavailableError(
            "PDF workflow is not enabled; configure RAG and workflow storage"
        )
    return service


@router.post(
    "/pdf-report",
    response_model=WorkflowStatusResponse,
    summary="Start a PDF report workflow",
    description=(
        "Upload a PDF and run the graph synchronously until the first "
        "approval interrupt or final report is generated."
    ),
)
async def create_pdf_report_workflow(
    file: Annotated[UploadFile, File(description="PDF document")],
    request: Request,
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[PDFReportWorkflowService, Depends(get_workflow_service)],
    topic: Annotated[str | None, Form(description="Optional report topic")] = None,
) -> WorkflowStatusResponse:
    settings = get_settings()
    try:
        content = await file.read(settings.rag_max_upload_bytes + 1)
    finally:
        await file.close()
    if len(content) > settings.rag_max_upload_bytes:
        raise RAGDocumentTooLargeError(
            f"PDF 文件超过限制（最多 {settings.rag_max_upload_bytes} 字节）。"
        )
    identity = request.state.context.identity
    view = await service.start(
        pdf_bytes=content,
        filename=file.filename,
        owner_key_hash=resolve_tenant_scope(identity),
        topic=topic,
    )
    return WorkflowStatusResponse.from_view(view)


class WorkflowRunSummaryResponse(BaseModel):
    thread_id: str
    status: str
    stage: str
    filename: str | None = None
    report_topic: str | None = None
    created_at: datetime | None = None


@router.get(
    "",
    response_model=list[WorkflowRunSummaryResponse],
    summary="List the tenant's workflow runs (newest first)",
)
async def list_workflows(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[PDFReportWorkflowService, Depends(get_workflow_service)],
    limit: int = Query(default=20, ge=1, le=100),
) -> list[WorkflowRunSummaryResponse]:
    identity = request.state.context.identity
    owner = resolve_tenant_scope(identity)
    runs = await service.list_runs(owner, limit=limit)
    return [
        WorkflowRunSummaryResponse(
            thread_id=run.thread_id,
            status=run.status.value,
            stage=run.stage.value,
            filename=run.filename,
            report_topic=run.report_topic,
            created_at=run.created_at,
        )
        for run in runs
    ]


@router.get(
    "/{thread_id}",
    response_model=WorkflowStatusResponse,
    summary="Get PDF report workflow status",
)
async def get_workflow_status(
    thread_id: str,
    request: Request,
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[PDFReportWorkflowService, Depends(get_workflow_service)],
) -> WorkflowStatusResponse:
    identity = request.state.context.identity
    view = await service.get_status(
        thread_id, owner_key_hash=resolve_tenant_scope(identity)
    )
    return WorkflowStatusResponse.from_view(view)


@router.post(
    "/{thread_id}/approve",
    response_model=WorkflowStatusResponse,
    summary="Approve the pending report draft",
)
async def approve_workflow(
    thread_id: str,
    request: Request,
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[PDFReportWorkflowService, Depends(get_workflow_service)],
) -> WorkflowStatusResponse:
    identity = request.state.context.identity
    view = await service.approve(
        thread_id, owner_key_hash=resolve_tenant_scope(identity)
    )
    return WorkflowStatusResponse.from_view(view)


@router.post(
    "/{thread_id}/reject",
    response_model=WorkflowStatusResponse,
    summary="Reject the pending report draft with feedback",
)
async def reject_workflow(
    thread_id: str,
    req: WorkflowRejectRequest,
    request: Request,
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[PDFReportWorkflowService, Depends(get_workflow_service)],
) -> WorkflowStatusResponse:
    identity = request.state.context.identity
    view = await service.reject(
        thread_id,
        owner_key_hash=resolve_tenant_scope(identity),
        feedback=req.feedback,
    )
    return WorkflowStatusResponse.from_view(view)
