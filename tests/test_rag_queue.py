import asyncio
from datetime import UTC, datetime

import pytest

from app.rag.ingestion import IngestedDocument
from app.rag.queue import IngestionTask, IngestionTaskStatus, RAGIngestionQueue

OWNER_A = "a" * 64
OWNER_B = "b" * 64


class FakeIngestionService:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[tuple[bytes, str | None, str]] = []

    async def ingest_pdf(
        self,
        content: bytes,
        *,
        filename: str | None,
        owner_key_hash: str,
    ) -> IngestedDocument:
        self.calls.append((content, filename, owner_key_hash))
        if self.should_fail:
            raise RuntimeError("embedding unavailable")
        return IngestedDocument(
            document_id="123e4567-e89b-12d3-a456-426614174000",
            filename=filename or "document.pdf",
            text_characters=10,
            chunk_count=1,
            content_sha256="c" * 64,
            embedding_model="test-embed",
            created_at=datetime.now(UTC),
        )


async def _wait_for_status(
    queue: RAGIngestionQueue,
    task_id: str,
    owner_key_hash: str,
    status: IngestionTaskStatus,
) -> IngestionTask:
    for _ in range(100):
        task = queue.get_task(task_id, owner_key_hash=owner_key_hash)
        if task is not None and task.status == status:
            return task
        await asyncio.sleep(0)
    raise AssertionError(f"task did not reach {status}")


@pytest.mark.asyncio
async def test_queue_processes_job_and_passes_owner_hash() -> None:
    service = FakeIngestionService()
    queue = RAGIngestionQueue(service)
    await queue.start()
    try:
        task = await queue.submit(
            b"pdf-bytes",
            filename="brief.pdf",
            owner_key_hash=OWNER_A,
        )
        completed = await _wait_for_status(
            queue, task.task_id, OWNER_A, IngestionTaskStatus.COMPLETED
        )
    finally:
        await queue.stop()

    assert completed.document_id == "123e4567-e89b-12d3-a456-426614174000"
    assert service.calls == [(b"pdf-bytes", "brief.pdf", OWNER_A)]
    assert queue.get_task(task.task_id, owner_key_hash=OWNER_B) is None


@pytest.mark.asyncio
async def test_queue_records_failure() -> None:
    queue = RAGIngestionQueue(FakeIngestionService(should_fail=True))
    await queue.start()
    task = await queue.submit(
        b"pdf-bytes",
        filename="broken.pdf",
        owner_key_hash=OWNER_A,
    )
    try:
        failed = await _wait_for_status(
            queue, task.task_id, OWNER_A, IngestionTaskStatus.FAILED
        )
    finally:
        await queue.stop()

    assert failed.error == "PDF 入库失败，请稍后重试。"
    assert failed.error_code == "RAG_INGESTION_FAILED"
    assert failed.document_id is None


@pytest.mark.asyncio
async def test_stop_marks_queued_jobs_failed_without_persisting_pdf_bytes() -> None:
    queue = RAGIngestionQueue(FakeIngestionService(), max_queue_size=2)
    await queue.start()
    first = await queue.submit(b"first", filename="first.pdf", owner_key_hash=OWNER_A)
    second = await queue.submit(
        b"second", filename="second.pdf", owner_key_hash=OWNER_A
    )
    await queue.stop()

    assert queue.get_task(first.task_id, owner_key_hash=OWNER_A) is not None
    stopped = queue.get_task(second.task_id, owner_key_hash=OWNER_A)
    assert stopped is not None
    assert stopped.status == IngestionTaskStatus.FAILED
    assert stopped.error == "RAG ingestion worker stopped before processing"
