"""Pydantic schemas for the PDF report workflow API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.workflow_service import WorkflowStatusView
from app.workflows.models import WorkflowRunStage, WorkflowRunStatus


class WorkflowStatusResponse(BaseModel):
    thread_id: str
    status: WorkflowRunStatus
    stage: WorkflowRunStage
    filename: str | None = None
    report_topic: str | None = None
    page_count: int | None = None
    retrieval_query: str | None = None
    references: int | None = None
    retrieval_warning: str | None = None
    draft_summary: str | None = None
    report: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    revision_count: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_view(cls, view: WorkflowStatusView) -> WorkflowStatusResponse:
        return cls(
            thread_id=view.thread_id,
            status=view.status,
            stage=view.stage,
            filename=view.filename,
            report_topic=view.report_topic,
            page_count=view.page_count,
            retrieval_query=view.retrieval_query,
            references=view.references,
            retrieval_warning=view.retrieval_warning,
            draft_summary=view.draft_summary,
            report=view.report,
            model=view.model,
            prompt_tokens=view.prompt_tokens,
            completion_tokens=view.completion_tokens,
            revision_count=view.revision_count,
            error_code=view.error_code,
            error_message=view.error_message,
            created_at=view.created_at,
            updated_at=view.updated_at,
        )


class WorkflowRejectRequest(BaseModel):
    feedback: str = Field(min_length=1, max_length=4000)
