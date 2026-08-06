from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.rag.queue import IngestionTask, IngestionTaskStatus


class RAGDocumentResponse(BaseModel):
    """Safe metadata returned for an indexed knowledge-base document."""

    document_id: UUID
    filename: str
    text_characters: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    content_sha256: str
    embedding_model: str
    created_at: datetime | None


class RAGDocumentListResponse(BaseModel):
    data: list[RAGDocumentResponse]


class RAGDocumentPreviewResponse(BaseModel):
    document_id: UUID
    filename: str
    content: str
    truncated: bool


class RAGIngestionTaskResponse(BaseModel):
    """Safe metadata for one asynchronous PDF ingestion task."""

    task_id: str
    document_id: UUID | None
    filename: str
    status: IngestionTaskStatus
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    error_code: str | None = None

    @classmethod
    def from_task(cls, task: IngestionTask) -> "RAGIngestionTaskResponse":
        return cls(
            task_id=task.task_id,
            document_id=(
                UUID(task.document_id) if task.document_id is not None else None
            ),
            filename=task.filename,
            status=task.status,
            created_at=task.created_at,
            updated_at=task.updated_at,
            error=task.error,
            error_code=task.error_code,
        )
