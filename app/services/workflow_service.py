"""Service boundary for the stateful PDF report workflow."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot

from app.exceptions.base import (
    ConflictError,
    ProviderError,
    ProviderUnavailableError,
    RAGDocumentTooLargeError,
    RAGDocumentValidationError,
    RAGUnavailableError,
    ValidationError,
    WorkflowNotFoundError,
)
from app.rag.pdf_extractor import normalize_pdf_filename
from app.workflows.models import WorkflowRun, WorkflowRunStage, WorkflowRunStatus
from app.workflows.pdf_report import PDFReportState, PDFReportWorkflow
from app.workflows.repository import WorkflowRunRepository

logger = logging.getLogger(__name__)

_DEFAULT_WORK_DIR = Path("output/workflows")
_DEFAULT_MAX_UPLOAD_BYTES = 10_000_000
_DEFAULT_MAX_STATUS_CHARACTERS = 100_000


@dataclass(frozen=True)
class WorkflowStatusView:
    """Safe status projection returned to workflow API callers."""

    thread_id: str
    status: WorkflowRunStatus
    stage: WorkflowRunStage
    filename: str | None
    report_topic: str | None
    page_count: int | None
    retrieval_query: str | None
    references: int | None
    retrieval_warning: str | None
    draft_summary: str | None
    report: str | None
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    revision_count: int | None
    error_code: str | None
    error_message: str | None
    created_at: datetime | None
    updated_at: datetime | None


class PDFReportWorkflowService:
    """Execute and resume ``PDFReportWorkflow`` with durable state."""

    def __init__(
        self,
        *,
        workflow: PDFReportWorkflow,
        checkpointer: BaseCheckpointSaver,
        run_repository: WorkflowRunRepository,
        work_dir: Path = _DEFAULT_WORK_DIR,
        max_upload_bytes: int = _DEFAULT_MAX_UPLOAD_BYTES,
        max_status_characters: int = _DEFAULT_MAX_STATUS_CHARACTERS,
    ) -> None:
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be greater than zero")
        if max_status_characters < 1:
            raise ValueError("max_status_characters must be greater than zero")
        self._workflow = workflow
        self._checkpointer = checkpointer
        self._run_repository = run_repository
        self._work_dir = work_dir
        self._max_upload_bytes = max_upload_bytes
        self._max_status_characters = max_status_characters
        self._graph: CompiledStateGraph[PDFReportState, Any, Any, Any] | None = None

    async def start(
        self,
        *,
        pdf_bytes: bytes,
        filename: str | None,
        owner_key_hash: str,
        topic: str | None = None,
        model: str | None = None,
        require_approval: bool = True,
        max_revisions: int = 2,
        thread_id: str | None = None,
    ) -> WorkflowStatusView:
        if len(pdf_bytes) > self._max_upload_bytes:
            raise RAGDocumentTooLargeError(
                f"PDF 文件超过限制（最多 {self._max_upload_bytes} 字节）。"
            )

        thread_id = thread_id or str(uuid.uuid4())
        safe_filename = normalize_pdf_filename(filename)
        safe_topic = _bounded_text(topic, 1000) if topic is not None else None
        run_dir = self._work_dir / thread_id
        run_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = run_dir / safe_filename
        output_path = run_dir / "report.md"

        initial: PDFReportState = {
            "pdf_path": str(pdf_path),
            "report_topic": safe_topic,
            "owner_key_hash": owner_key_hash,
            "model": model,
            "output_path": str(output_path),
            "require_approval": require_approval,
            "max_revisions": max_revisions,
        }
        run = WorkflowRun(
            thread_id=thread_id,
            owner_key_hash=owner_key_hash,
            status=WorkflowRunStatus.RUNNING,
            stage=WorkflowRunStage.STARTING,
            filename=safe_filename,
            report_topic=safe_topic,
        )
        created: WorkflowRun | None = None
        try:
            await asyncio.to_thread(pdf_path.write_bytes, pdf_bytes)
            created = await self._run_repository.create(run)
            result = await self._invoke(initial, thread_id)
        except Exception as exc:
            if created is not None:
                await self._record_failure(created, exc)
            raise
        finally:
            pdf_path.unlink(missing_ok=True)
        return await self._view_from_result(created, cast(PDFReportState, result))

    async def approve(self, thread_id: str, owner_key_hash: str) -> WorkflowStatusView:
        updated = await self._run_repository.update_status_if(
            thread_id,
            owner_key_hash,
            expected_status=WorkflowRunStatus.PENDING_APPROVAL,
            new_status=WorkflowRunStatus.RUNNING,
            new_stage=WorkflowRunStage.STARTING,
        )
        if updated is None:
            existing = await self._run_repository.get(thread_id, owner_key_hash)
            if existing is None:
                raise WorkflowNotFoundError("Workflow not found.")
            raise ConflictError("Workflow is not awaiting approval.")
        try:
            result = await self._resume(
                updated.thread_id,
                {"decision": "approved", "feedback": ""},
            )
        except Exception as exc:
            await self._record_failure(updated, exc)
            raise
        return await self._view_from_result(updated, cast(PDFReportState, result))

    async def reject(
        self,
        thread_id: str,
        owner_key_hash: str,
        *,
        feedback: str,
    ) -> WorkflowStatusView:
        if not feedback.strip():
            raise ValidationError("feedback must not be empty.")
        updated = await self._run_repository.update_status_if(
            thread_id,
            owner_key_hash,
            expected_status=WorkflowRunStatus.PENDING_APPROVAL,
            new_status=WorkflowRunStatus.RUNNING,
            new_stage=WorkflowRunStage.STARTING,
        )
        if updated is None:
            existing = await self._run_repository.get(thread_id, owner_key_hash)
            if existing is None:
                raise WorkflowNotFoundError("Workflow not found.")
            raise ConflictError("Workflow is not awaiting approval.")
        try:
            result = await self._resume(
                updated.thread_id,
                {"decision": "rejected", "feedback": feedback},
            )
        except Exception as exc:
            await self._record_failure(updated, exc)
            raise
        return await self._view_from_result(updated, cast(PDFReportState, result))

    async def get_status(
        self, thread_id: str, owner_key_hash: str
    ) -> WorkflowStatusView:
        run = await self._require_run(thread_id, owner_key_hash)
        snapshot = await self._get_state_snapshot(run.thread_id, owner_key_hash)
        values = snapshot.values if snapshot is not None else {}
        return self._build_view(run, cast(Mapping[str, Any], values))

    def _get_graph(
        self,
    ) -> CompiledStateGraph[PDFReportState, Any, Any, Any]:
        if self._graph is None:
            self._graph = self._workflow.build()
        return self._graph

    async def _invoke(
        self,
        initial: PDFReportState,
        thread_id: str,
    ) -> dict[str, object]:
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        graph = self._get_graph()
        return cast(
            dict[str, object],
            await graph.ainvoke(initial, config),
        )

    async def _resume(
        self,
        thread_id: str,
        decision: dict[str, object],
    ) -> dict[str, object]:
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        graph = self._get_graph()
        return cast(
            dict[str, object],
            await graph.ainvoke(Command(resume=decision), config),
        )

    async def _require_run(
        self,
        thread_id: str,
        owner_key_hash: str,
    ) -> WorkflowRun:
        normalized = self._normalize_thread_id(thread_id)
        if normalized is None:
            raise WorkflowNotFoundError("Workflow not found.")
        run = await self._run_repository.get(normalized, owner_key_hash)
        if run is None:
            raise WorkflowNotFoundError("Workflow not found.")
        return run

    async def _get_state_snapshot(
        self, thread_id: str, owner_key_hash: str
    ) -> StateSnapshot | None:
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        graph = self._get_graph()
        snapshot = await graph.aget_state(config)
        values = snapshot.values
        if not isinstance(values, Mapping) or not values:
            return None
        if _optional_str(values.get("owner_key_hash")) != owner_key_hash:
            return None
        return snapshot

    async def _view_from_result(
        self,
        run: WorkflowRun,
        result: PDFReportState,
    ) -> WorkflowStatusView:
        updated = await self._finalize_run(run, result)
        return self._build_view(updated, result)

    async def _finalize_run(
        self,
        run: WorkflowRun,
        result: PDFReportState,
    ) -> WorkflowRun:
        if result.get("report_path"):
            run.status = WorkflowRunStatus.COMPLETED
            run.stage = WorkflowRunStage.COMPLETED
        elif result.get("__interrupt__"):
            run.status = WorkflowRunStatus.PENDING_APPROVAL
            run.stage = WorkflowRunStage.AWAITING_APPROVAL
        elif result.get("approval") == "rejected":
            run.status = WorkflowRunStatus.REJECTED
            run.stage = WorkflowRunStage.REJECTED
        else:
            run.status = WorkflowRunStatus.RUNNING
            run.stage = WorkflowRunStage.STARTING
        run.filename = result.get("filename") or run.filename
        run.report_topic = result.get("report_topic") or run.report_topic
        run.error_code = None
        run.error_message = None
        run.updated_at = datetime.now(UTC)
        updated = await self._run_repository.update(run)
        return updated or run

    async def _record_failure(self, run: WorkflowRun, exc: Exception) -> None:
        error_code, error_message = self._safe_error(exc)
        run.status = WorkflowRunStatus.FAILED
        run.stage = WorkflowRunStage.FAILED
        run.error_code = error_code
        run.error_message = error_message
        run.updated_at = datetime.now(UTC)
        await self._run_repository.update(run)
        logger.warning(
            "workflow_failed thread_id=%s error_code=%s",
            run.thread_id,
            error_code,
        )

    def _build_view(
        self,
        run: WorkflowRun,
        values: Mapping[str, Any],
    ) -> WorkflowStatusView:
        report_path = values.get("report_path")
        report = (
            self._read_report(report_path) if isinstance(report_path, str) else None
        )
        references = values.get("references")
        references_count = len(references) if isinstance(references, list) else None
        return WorkflowStatusView(
            thread_id=run.thread_id,
            status=run.status,
            stage=run.stage,
            filename=run.filename,
            report_topic=run.report_topic,
            page_count=_optional_int(values.get("page_count")),
            retrieval_query=_optional_str(values.get("retrieval_query")),
            references=references_count,
            retrieval_warning=_optional_str(values.get("retrieval_warning")),
            draft_summary=_bounded_text(
                _optional_str(values.get("analysis")),
                self._max_status_characters,
            ),
            report=report,
            model=_optional_str(values.get("model_name")),
            prompt_tokens=_optional_int(values.get("prompt_tokens")),
            completion_tokens=_optional_int(values.get("completion_tokens")),
            revision_count=_optional_int(values.get("revision_count")),
            error_code=run.error_code,
            error_message=run.error_message,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    def _read_report(self, report_path: str) -> str | None:
        try:
            path = Path(report_path)
            if not path.is_file():
                return None
            return _bounded_text(
                path.read_text(encoding="utf-8"),
                self._max_status_characters,
            )
        except OSError:
            logger.warning("workflow report unreadable path=%s", report_path)
            return None

    @staticmethod
    def _normalize_thread_id(thread_id: str) -> str | None:
        try:
            return str(uuid.UUID(thread_id))
        except (ValueError, AttributeError, TypeError):
            return None

    @staticmethod
    def _safe_error(exc: Exception) -> tuple[str, str]:
        if isinstance(exc, RAGDocumentValidationError):
            return "RAG_DOCUMENT_INVALID", str(exc)
        if isinstance(exc, RAGDocumentTooLargeError):
            return "RAG_DOCUMENT_TOO_LARGE", str(exc)
        if isinstance(exc, RAGUnavailableError):
            return "RAG_UNAVAILABLE", str(exc)
        if isinstance(exc, ProviderUnavailableError):
            return "PROVIDER_UNAVAILABLE", str(exc)
        if isinstance(exc, ProviderError):
            return "PROVIDER_ERROR", str(exc)
        return "WORKFLOW_EXECUTION_FAILED", "Workflow execution failed."


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _bounded_text(value: str | None, max_characters: int) -> str | None:
    if value is None or len(value) <= max_characters:
        return value
    return value[:max_characters]
