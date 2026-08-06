from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.api.rag import get_rag_ingestion_queue, get_rag_ingestion_service
from app.auth.hash import hash_api_key
from app.main import app
from app.rag.ingestion import IngestedDocument, RAGIngestionService
from app.rag.queue import IngestionTask, IngestionTaskStatus, RAGIngestionQueue
from app.rag.vector_store import DocumentPreview, DocumentSummary

_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}


def _document() -> IngestedDocument:
    return IngestedDocument(
        document_id="123e4567-e89b-12d3-a456-426614174000",
        filename="brief.pdf",
        text_characters=120,
        chunk_count=2,
        content_sha256="a" * 64,
        embedding_model="nomic-embed-text",
        created_at=None,
    )


def test_upload_pdf_returns_queued_task_without_ingesting_in_request() -> None:
    queue = AsyncMock(spec=RAGIngestionQueue)
    queue.submit.return_value = IngestionTask(
        task_id="task-1",
        document_id=None,
        filename="brief.pdf",
        status=IngestionTaskStatus.QUEUED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        owner_key_hash=hash_api_key(
            _AUTH_HEADERS["Authorization"].removeprefix("Bearer ")
        ),
    )
    app.dependency_overrides[get_rag_ingestion_queue] = lambda: queue

    try:
        response = TestClient(app).post(
            "/api/v1/rag/documents",
            files={"file": ("brief.pdf", b"%PDF-fake", "application/pdf")},
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_rag_ingestion_queue, None)

    assert response.status_code == 202
    assert response.json()["task_id"] == "task-1"
    assert response.json()["document_id"] is None
    assert response.json()["status"] == "queued"
    queue.submit.assert_awaited_once()
    assert queue.submit.call_args.kwargs["owner_key_hash"] == hash_api_key(
        _AUTH_HEADERS["Authorization"].removeprefix("Bearer ")
    )


def test_list_documents_returns_safe_document_metadata() -> None:
    service = AsyncMock(spec=RAGIngestionService)
    service.list_documents.return_value = [
        DocumentSummary(
            document_id="123e4567-e89b-12d3-a456-426614174000",
            filename="brief.pdf",
            content_sha256="a" * 64,
            embedding_model="nomic-embed-text",
            embedding_dimensions=768,
            created_at=None,
            chunk_count=2,
            text_characters=120,
        )
    ]
    app.dependency_overrides[get_rag_ingestion_service] = lambda: service

    try:
        response = TestClient(app).get(
            "/api/v1/rag/documents",
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_rag_ingestion_service, None)

    assert response.status_code == 200
    assert response.json()["data"][0]["chunk_count"] == 2
    assert "embedding_dimensions" not in response.json()["data"][0]


def test_task_status_is_hidden_from_a_different_api_key() -> None:
    queue = AsyncMock(spec=RAGIngestionQueue)
    queue.get_task.return_value = None
    app.dependency_overrides[get_rag_ingestion_queue] = lambda: queue

    try:
        response = TestClient(app).get(
            "/api/v1/rag/tasks/task-1",
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_rag_ingestion_queue, None)

    assert response.status_code == 404
    queue.get_task.assert_called_once_with(
        "task-1", owner_key_hash=hash_api_key("sk-test-integration")
    )


def test_delete_document_is_owner_scoped_and_requires_uuid() -> None:
    service = AsyncMock(spec=RAGIngestionService)
    service.delete_document.return_value = True
    app.dependency_overrides[get_rag_ingestion_service] = lambda: service
    document_id = "123e4567-e89b-12d3-a456-426614174000"

    try:
        response = TestClient(app).delete(
            f"/api/v1/rag/documents/{document_id}",
            headers=_AUTH_HEADERS,
        )
        invalid = TestClient(app).delete(
            "/api/v1/rag/documents/not-a-uuid",
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_rag_ingestion_service, None)

    assert response.status_code == 204
    assert invalid.status_code == 422
    service.delete_document.assert_awaited_once_with(
        owner_key_hash=hash_api_key("sk-test-integration"),
        document_id=document_id,
    )


def test_preview_document_returns_bounded_text_for_authenticated_owner() -> None:
    service = AsyncMock(spec=RAGIngestionService)
    service.get_document_preview.return_value = DocumentPreview(
        document_id="123e4567-e89b-12d3-a456-426614174000",
        filename="brief.pdf",
        content="safe extracted text",
        truncated=False,
    )
    app.dependency_overrides[get_rag_ingestion_service] = lambda: service
    document_id = "123e4567-e89b-12d3-a456-426614174000"

    try:
        response = TestClient(app).get(
            f"/api/v1/rag/documents/{document_id}/preview",
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_rag_ingestion_service, None)

    assert response.status_code == 200
    assert response.json() == {
        "document_id": document_id,
        "filename": "brief.pdf",
        "content": "safe extracted text",
        "truncated": False,
    }
    service.get_document_preview.assert_awaited_once_with(
        owner_key_hash=hash_api_key("sk-test-integration"),
        document_id=document_id,
    )
