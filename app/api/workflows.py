"""HTTP API for the stateful LangGraph PDF report workflow."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.auth.models import APIKey
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
    view = await service.start(
        pdf_bytes=content,
        filename=file.filename,
        owner_key_hash=api_key.key,
        topic=topic,
    )
    return WorkflowStatusResponse.from_view(view)


@router.get(
    "/{thread_id}",
    response_model=WorkflowStatusResponse,
    summary="Get PDF report workflow status",
)
async def get_workflow_status(
    thread_id: str,
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[PDFReportWorkflowService, Depends(get_workflow_service)],
) -> WorkflowStatusResponse:
    view = await service.get_status(thread_id, owner_key_hash=api_key.key)
    return WorkflowStatusResponse.from_view(view)


@router.post(
    "/{thread_id}/approve",
    response_model=WorkflowStatusResponse,
    summary="Approve the pending report draft",
)
async def approve_workflow(
    thread_id: str,
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[PDFReportWorkflowService, Depends(get_workflow_service)],
) -> WorkflowStatusResponse:
    view = await service.approve(thread_id, owner_key_hash=api_key.key)
    return WorkflowStatusResponse.from_view(view)


@router.post(
    "/{thread_id}/reject",
    response_model=WorkflowStatusResponse,
    summary="Reject the pending report draft with feedback",
)
async def reject_workflow(
    thread_id: str,
    request: WorkflowRejectRequest,
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[PDFReportWorkflowService, Depends(get_workflow_service)],
) -> WorkflowStatusResponse:
    view = await service.reject(
        thread_id,
        owner_key_hash=api_key.key,
        feedback=request.feedback,
    )
    return WorkflowStatusResponse.from_view(view)
