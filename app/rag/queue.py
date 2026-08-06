"""In-memory asynchronous queue for PDF knowledge-base ingestion."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from app.exceptions.base import (
    ConflictError,
    ProviderError,
    ProviderUnavailableError,
    RAGDocumentValidationError,
    RAGStorageUnavailableError,
)
from app.rag.ingestion import IngestedDocument
from app.rag.vector_store import validate_owner_key_hash

logger = logging.getLogger(__name__)


class IngestionService(Protocol):
    async def ingest_pdf(
        self,
        content: bytes,
        *,
        filename: str | None,
        owner_key_hash: str,
    ) -> IngestedDocument: ...


class IngestionTaskStatus(StrEnum):
    """Lifecycle states exposed by the ingestion task API."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class IngestionTask:
    """Safe task metadata; the owner hash is intentionally not exposed by API."""

    task_id: str
    document_id: str | None
    filename: str
    status: IngestionTaskStatus
    created_at: datetime
    updated_at: datetime
    owner_key_hash: str
    error: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class _IngestionWorkItem:
    task_id: str
    content: bytes
    filename: str | None
    owner_key_hash: str


class RAGIngestionQueue:
    """Consume PDF ingestion jobs without doing provider work in request handlers.

    Task metadata and queued PDF bytes live only in process memory. Bytes are
    retained until the worker finishes the job and are never persisted.
    """

    def __init__(
        self,
        ingestion_service: IngestionService,
        *,
        max_queue_size: int = 100,
    ) -> None:
        self._ingestion_service = ingestion_service
        self._queue: asyncio.Queue[_IngestionWorkItem | None] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self._tasks: dict[str, IngestionTask] = {}
        self._worker_task: asyncio.Task[None] | None = None
        self._accepting = False
        self._stop_requested = False

    @property
    def is_running(self) -> bool:
        """Return whether the background worker is currently running."""

        return self._worker_task is not None and not self._worker_task.done()

    async def start(self) -> None:
        """Start one worker for the current application event loop."""

        if self.is_running:
            return
        if self._stop_requested:
            raise RuntimeError("RAG ingestion queue cannot be restarted after stop")
        self._accepting = True
        self._worker_task = asyncio.create_task(
            self._worker(), name="rag-ingestion-worker"
        )

    async def stop(self) -> None:
        """Discard queued work, mark unfinished tasks, and stop the worker."""

        self._accepting = False
        self._stop_requested = True
        worker_task = self._worker_task
        if worker_task is None:
            self._mark_unfinished_tasks_failed("RAG ingestion worker stopped")
            return

        self._discard_queued_work("RAG ingestion worker stopped before processing")
        if not worker_task.done():
            await self._queue.put(None)
        try:
            await worker_task
        finally:
            self._worker_task = None
            self._mark_unfinished_tasks_failed("RAG ingestion worker stopped")

    async def submit(
        self,
        content: bytes,
        *,
        filename: str | None,
        owner_key_hash: str,
    ) -> IngestionTask:
        """Register and enqueue a job using only a validated API-key hash."""

        if not self._accepting or not self.is_running:
            raise RuntimeError("RAG ingestion worker is not running")

        now = datetime.now(UTC)
        task_id = str(uuid.uuid4())
        owner_hash = validate_owner_key_hash(owner_key_hash)
        task = IngestionTask(
            task_id=task_id,
            document_id=None,
            filename=filename or "document.pdf",
            status=IngestionTaskStatus.QUEUED,
            created_at=now,
            updated_at=now,
            owner_key_hash=owner_hash,
        )
        work_item = _IngestionWorkItem(
            task_id=task_id,
            content=content,
            filename=filename,
            owner_key_hash=owner_hash,
        )
        self._tasks[task_id] = task
        try:
            self._queue.put_nowait(work_item)
        except asyncio.QueueFull:
            self._tasks.pop(task_id, None)
            raise
        return replace(task)

    def get_task(self, task_id: str, *, owner_key_hash: str) -> IngestionTask | None:
        """Return a task only when it belongs to the requesting API key."""

        task = self._tasks.get(task_id)
        if task is None:
            return None
        owner_hash = validate_owner_key_hash(owner_key_hash)
        if task.owner_key_hash != owner_hash:
            return None
        return replace(task)

    async def _worker(self) -> None:
        while True:
            work_item = await self._queue.get()
            try:
                if work_item is None:
                    return
                await self._process(work_item)
            finally:
                self._queue.task_done()

    async def _process(self, work_item: _IngestionWorkItem) -> None:
        self._update_task(
            work_item.task_id,
            status=IngestionTaskStatus.PROCESSING,
            error=None,
        )
        try:
            document = await self._ingestion_service.ingest_pdf(
                work_item.content,
                filename=work_item.filename,
                owner_key_hash=work_item.owner_key_hash,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("RAG ingestion task failed: %s", work_item.task_id)
            self._update_task(
                work_item.task_id,
                status=IngestionTaskStatus.FAILED,
                error=_safe_error_message(exc),
                error_code=_safe_error_code(exc),
            )
            return

        self._update_task(
            work_item.task_id,
            status=IngestionTaskStatus.COMPLETED,
            document_id=document.document_id,
            error=None,
        )

    def _update_task(
        self,
        task_id: str,
        *,
        status: IngestionTaskStatus,
        document_id: str | None = None,
        error: str | None,
        error_code: str | None = None,
    ) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        self._tasks[task_id] = replace(
            task,
            status=status,
            document_id=document_id if document_id is not None else task.document_id,
            updated_at=datetime.now(UTC),
            error=error,
            error_code=error_code,
        )

    def _discard_queued_work(self, message: str) -> None:
        while True:
            try:
                work_item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                if work_item is not None:
                    self._update_task(
                        work_item.task_id,
                        status=IngestionTaskStatus.FAILED,
                        error=message,
                        error_code="RAG_INGESTION_FAILED",
                    )
            finally:
                self._queue.task_done()

    def _mark_unfinished_tasks_failed(self, message: str) -> None:
        for task_id, task in tuple(self._tasks.items()):
            if task.status in (
                IngestionTaskStatus.QUEUED,
                IngestionTaskStatus.PROCESSING,
            ):
                self._tasks[task_id] = replace(
                    task,
                    status=IngestionTaskStatus.FAILED,
                    updated_at=datetime.now(UTC),
                    error=message,
                    error_code="RAG_INGESTION_FAILED",
                )


def _safe_error_message(exc: Exception) -> str:
    """Return a user-safe task failure message without provider details."""

    if isinstance(exc, RAGDocumentValidationError):
        return "PDF 无法解析或不包含可提取文本。"
    if isinstance(exc, ConflictError):
        return "文档与当前知识库中的已有文档冲突。"
    if isinstance(exc, ProviderUnavailableError):
        return "Embedding 服务暂时不可用，请稍后重试。"
    if isinstance(exc, ProviderError):
        return "Embedding 服务处理失败，请稍后重试。"
    if isinstance(exc, RAGStorageUnavailableError):
        return "知识库存储暂时不可用，请稍后重试。"
    return "PDF 入库失败，请稍后重试。"


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, RAGDocumentValidationError):
        return "RAG_DOCUMENT_INVALID"
    if isinstance(exc, ConflictError):
        return "CONFLICT_ERROR"
    if isinstance(exc, ProviderUnavailableError):
        return "RAG_EMBEDDING_UNAVAILABLE"
    if isinstance(exc, ProviderError):
        return "RAG_EMBEDDING_FAILED"
    if isinstance(exc, RAGStorageUnavailableError):
        return "RAG_STORAGE_UNAVAILABLE"
    return "RAG_INGESTION_FAILED"
