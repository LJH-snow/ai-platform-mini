import asyncio
import time
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)

from app.auth.models import APIKey
from app.core.container import (
    provide_quota_service,
    provide_rag_ingestion_queue,
    provide_rag_ingestion_service,
    provide_usage_collector,
)
from app.core.context import RequestContext
from app.exceptions.base import RAGDocumentTooLargeError, RAGUnavailableError
from app.quota.lifecycle import ReservationLifecycle
from app.quota.models import QuotaReservation
from app.quota.service import QuotaService
from app.quota.token_estimator import estimate_prompt_tokens
from app.rag.ingestion import IngestedDocument, RAGIngestionService
from app.rag.queue import IngestionTask, RAGIngestionQueue
from app.rag.service import PreparedRAGRequest, RAGService
from app.rag.vector_store import DocumentSummary
from app.ratelimit.dependencies import require_rate_limit
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.rag import (
    RAGDocumentListResponse,
    RAGDocumentPreviewResponse,
    RAGDocumentResponse,
    RAGIngestionTaskResponse,
)
from app.usage.collector import UsageCollector

router = APIRouter(prefix="/api/v1", tags=["rag"])


def get_rag_ingestion_service() -> RAGIngestionService:
    service = provide_rag_ingestion_service()
    if service is None:
        raise RAGUnavailableError("RAG is not enabled")
    return service


def _to_document_response(
    document: IngestedDocument | DocumentSummary,
) -> RAGDocumentResponse:
    return RAGDocumentResponse(
        document_id=UUID(document.document_id),
        filename=document.filename,
        text_characters=document.text_characters,
        chunk_count=document.chunk_count,
        content_sha256=document.content_sha256,
        embedding_model=document.embedding_model,
        created_at=document.created_at,
    )


def get_rag_ingestion_queue() -> RAGIngestionQueue:
    queue = provide_rag_ingestion_queue()
    if queue is None:
        raise RAGUnavailableError("RAG is not enabled")
    return queue


def _to_task_response(task: IngestionTask) -> RAGIngestionTaskResponse:
    return RAGIngestionTaskResponse.from_task(task)


def get_rag_service() -> RAGService:
    from app.core.container import provide_rag_service

    service = provide_rag_service()
    if service is None:
        raise RAGUnavailableError("RAG is not enabled")
    return service


@router.post(
    "/rag/documents",
    response_model=RAGIngestionTaskResponse,
    status_code=202,
    summary="Queue a PDF document for indexing",
)
async def upload_rag_document(
    file: Annotated[UploadFile, File(description="PDF document")],
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    ingestion_queue: Annotated[RAGIngestionQueue, Depends(get_rag_ingestion_queue)],
) -> RAGIngestionTaskResponse:
    from app.core.settings import get_settings

    settings = get_settings()
    try:
        content = await file.read(settings.rag_max_upload_bytes + 1)
        if len(content) > settings.rag_max_upload_bytes:
            raise RAGDocumentTooLargeError(
                f"PDF 文件超过限制（最多 {settings.rag_max_upload_bytes} 字节）。"
            )
        try:
            task = await ingestion_queue.submit(
                content,
                filename=file.filename,
                owner_key_hash=api_key.key,
            )
        except asyncio.QueueFull as exc:
            raise RAGUnavailableError(
                "RAG ingestion queue is full; please retry later"
            ) from exc
        except RuntimeError as exc:
            raise RAGUnavailableError("RAG ingestion worker is not running") from exc
        return _to_task_response(task)
    finally:
        await file.close()


@router.get(
    "/rag/tasks/{task_id}",
    response_model=RAGIngestionTaskResponse,
    summary="Get PDF ingestion task status",
)
async def get_rag_ingestion_task(
    task_id: str,
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    ingestion_queue: Annotated[RAGIngestionQueue, Depends(get_rag_ingestion_queue)],
) -> RAGIngestionTaskResponse:
    task = ingestion_queue.get_task(task_id, owner_key_hash=api_key.key)
    if task is None:
        raise HTTPException(status_code=404, detail="Ingestion task not found")
    return _to_task_response(task)


@router.get(
    "/rag/documents",
    response_model=RAGDocumentListResponse,
    summary="List indexed PDF documents",
)
async def list_rag_documents(
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    ingestion_service: Annotated[
        RAGIngestionService, Depends(get_rag_ingestion_service)
    ],
) -> RAGDocumentListResponse:
    documents = await ingestion_service.list_documents(owner_key_hash=api_key.key)
    return RAGDocumentListResponse(
        data=[_to_document_response(document) for document in documents]
    )


@router.delete(
    "/rag/documents/{document_id}",
    status_code=204,
    summary="Delete an indexed PDF document",
)
async def delete_rag_document(
    document_id: UUID,
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    ingestion_service: Annotated[
        RAGIngestionService, Depends(get_rag_ingestion_service)
    ],
) -> Response:
    deleted = await ingestion_service.delete_document(
        owner_key_hash=api_key.key,
        document_id=str(document_id),
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return Response(status_code=204)


@router.get(
    "/rag/documents/{document_id}/preview",
    response_model=RAGDocumentPreviewResponse,
    summary="Preview extracted document text",
)
async def preview_rag_document(
    document_id: UUID,
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    ingestion_service: Annotated[
        RAGIngestionService, Depends(get_rag_ingestion_service)
    ],
) -> RAGDocumentPreviewResponse:
    preview = await ingestion_service.get_document_preview(
        owner_key_hash=api_key.key,
        document_id=str(document_id),
    )
    if preview is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return RAGDocumentPreviewResponse(
        document_id=UUID(preview.document_id),
        filename=preview.filename,
        content=preview.content,
        truncated=preview.truncated,
    )


@router.post(
    "/chat/rag",
    response_model=ChatResponse,
    summary="RAG-enhanced chat completion",
    description=(
        "Answer a question using retrieval-augmented generation. "
        "Retrieves relevant document chunks from the knowledge base, "
        "constructs an enhanced prompt, and calls the chat service."
    ),
)
async def create_rag_chat_completion(
    request: ChatRequest,
    http_request: Request,
    response: Response,
    # Authentication & rate limiting MUST resolve before RAG service
    # lookup — otherwise RAG_ENABLED=false leaks via 503 to
    # unauthenticated callers, bypassing the expected 401/403.
    api_key: Annotated[APIKey, Depends(require_rate_limit)],
    rag_service: Annotated[RAGService, Depends(get_rag_service)],
    collector: Annotated[UsageCollector, Depends(provide_usage_collector)],
    quota_service: Annotated[QuotaService, Depends(provide_quota_service)],
) -> ChatResponse:
    context: RequestContext = http_request.state.context

    # Phase 1: prepare — retrieve context and build enhanced prompt.
    prepared: PreparedRAGRequest = await rag_service.prepare(
        request, owner_key_hash=api_key.key
    )

    # Phase 2: quota reservation using the FINAL messages
    # (including RAG context) so that retrieval content is accounted for.
    reservation: QuotaReservation | None = await quota_service.reserve(
        api_key.key,
        max_tokens=request.max_tokens,
        prompt_tokens=estimate_prompt_tokens(prepared.messages),
    )

    async with ReservationLifecycle(reservation, quota_service) as lifecycle:
        start = time.monotonic()
        chat_response = await lifecycle.run(rag_service.answer(prepared))
        latency_ms = (time.monotonic() - start) * 1000
        await collector.record_chat(
            context=context, response=chat_response, latency_ms=latency_ms
        )
        await lifecycle.settle()

    remaining = getattr(http_request.state, "rate_limit_remaining", None)
    limit = getattr(http_request.state, "rate_limit_limit", None)
    reset_after = getattr(http_request.state, "rate_limit_reset_after", None)
    if remaining is not None and limit is not None:
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        if reset_after is not None:
            response.headers["X-RateLimit-Reset"] = str(reset_after)

    return chat_response
