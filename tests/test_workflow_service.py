"""Service-level tests for the PDF report workflow API boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.exceptions.base import ConflictError, ValidationError, WorkflowNotFoundError
from app.services.workflow_service import PDFReportWorkflowService, WorkflowStatusView
from app.workflows.memory_repository import InMemoryWorkflowRunRepository
from app.workflows.models import WorkflowRun, WorkflowRunStage, WorkflowRunStatus
from app.workflows.pdf_report import PDFReportWorkflow
from app.workflows.serde import create_workflow_serde
from workflow_fakes import FakeModel, FakePdfExtractor, FakeRetriever, make_reference

OWNER_A = "a" * 64
OWNER_B = "b" * 64


def build_service(
    tmp_path: Path,
    *,
    saver: InMemorySaver | None = None,
    repository: InMemoryWorkflowRunRepository | None = None,
    model_error: Exception | None = None,
) -> tuple[
    PDFReportWorkflowService,
    FakePdfExtractor,
    FakeRetriever,
    FakeModel,
    InMemoryWorkflowRunRepository,
    InMemorySaver,
]:
    extractor = FakePdfExtractor()
    retriever = FakeRetriever(references=(make_reference(),))
    model = FakeModel(error=model_error)
    checkpointer = saver or InMemorySaver(serde=create_workflow_serde())
    repository = repository or InMemoryWorkflowRunRepository()
    workflow = PDFReportWorkflow(
        extractor=extractor,
        retriever=retriever,
        model=model,
        checkpointer=checkpointer,
        max_document_characters=1_000,
        max_reference_characters=500,
    )
    service = PDFReportWorkflowService(
        workflow=workflow,
        checkpointer=checkpointer,
        run_repository=repository,
        work_dir=tmp_path / "workflows",
    )
    return service, extractor, retriever, model, repository, checkpointer


async def start_workflow(
    service: PDFReportWorkflowService,
    *,
    owner_key_hash: str = OWNER_A,
    require_approval: bool = True,
    max_revisions: int = 2,
    thread_id: str | None = None,
) -> WorkflowStatusView:
    return await service.start(
        pdf_bytes=b"%PDF-fake",
        filename="sample.pdf",
        owner_key_hash=owner_key_hash,
        topic="General review",
        require_approval=require_approval,
        max_revisions=max_revisions,
        thread_id=thread_id,
    )


async def test_start_pauses_for_approval(tmp_path: Path) -> None:
    service, _, _, model, _, _ = build_service(tmp_path)

    view = await start_workflow(service)

    assert view.status == WorkflowRunStatus.PENDING_APPROVAL
    assert view.stage == WorkflowRunStage.AWAITING_APPROVAL
    assert view.draft_summary == "Fake analysis"
    assert view.filename == "sample.pdf"
    assert len(model.messages) == 1


async def test_approve_generates_report(tmp_path: Path) -> None:
    service, _, _, _, _, _ = build_service(tmp_path)
    started = await start_workflow(service)

    completed = await service.approve(started.thread_id, OWNER_A)

    assert completed.status == WorkflowRunStatus.COMPLETED
    assert completed.stage == WorkflowRunStage.COMPLETED
    assert "Fake analysis" in (completed.report or "")
    report_path = tmp_path / "workflows" / completed.thread_id / "report.md"
    assert report_path.is_file()


async def test_reject_reanalyzes_with_feedback(tmp_path: Path) -> None:
    service, _, _, model, _, _ = build_service(tmp_path)
    started = await start_workflow(service)

    paused = await service.reject(
        started.thread_id,
        OWNER_A,
        feedback="add risk section",
    )

    assert paused.status == WorkflowRunStatus.PENDING_APPROVAL
    assert paused.stage == WorkflowRunStage.AWAITING_APPROVAL
    assert len(model.messages) == 2
    assert "add risk section" in model.messages[1][1][1]

    completed = await service.approve(paused.thread_id, OWNER_A)
    assert completed.status == WorkflowRunStatus.COMPLETED


async def test_reject_exhausts_max_revisions(tmp_path: Path) -> None:
    service, _, _, model, _, _ = build_service(tmp_path)
    started = await start_workflow(service, max_revisions=1)

    rejected = await service.reject(
        started.thread_id,
        OWNER_A,
        feedback="stop",
    )

    assert rejected.status == WorkflowRunStatus.REJECTED
    assert rejected.stage == WorkflowRunStage.REJECTED
    assert rejected.report is None
    assert len(model.messages) == 1


async def test_no_approval_completes_immediately(tmp_path: Path) -> None:
    service, _, _, _, _, _ = build_service(tmp_path)

    completed = await start_workflow(service, require_approval=False)

    assert completed.status == WorkflowRunStatus.COMPLETED
    assert completed.report is not None


async def test_new_service_instance_resumes_same_store(tmp_path: Path) -> None:
    service, _, _, _, repository, saver = build_service(tmp_path)
    started = await start_workflow(service)

    restarted, _, _, _, _, _ = build_service(
        tmp_path,
        saver=saver,
        repository=repository,
    )
    completed = await restarted.approve(started.thread_id, OWNER_A)

    assert completed.status == WorkflowRunStatus.COMPLETED
    assert completed.thread_id == started.thread_id


async def test_cross_tenant_access_raises_not_found(tmp_path: Path) -> None:
    service, _, _, _, _, _ = build_service(tmp_path)
    started = await start_workflow(service)

    with pytest.raises(WorkflowNotFoundError):
        await service.get_status(started.thread_id, OWNER_B)
    with pytest.raises(WorkflowNotFoundError):
        await service.approve(started.thread_id, OWNER_B)
    with pytest.raises(WorkflowNotFoundError):
        await service.reject(started.thread_id, OWNER_B, feedback="no")


async def test_invalid_thread_id_raises_not_found(tmp_path: Path) -> None:
    service, _, _, _, _, _ = build_service(tmp_path)

    with pytest.raises(WorkflowNotFoundError):
        await service.get_status("not-a-uuid", OWNER_A)


async def test_failure_is_recorded_with_safe_error(tmp_path: Path) -> None:
    thread_id = "00000000-0000-0000-0000-000000000000"
    service, _, _, _, _, _ = build_service(
        tmp_path,
        model_error=RuntimeError("secret provider detail"),
    )

    with pytest.raises(RuntimeError, match="secret provider detail"):
        await start_workflow(service, thread_id=thread_id)

    status = await service.get_status(thread_id, OWNER_A)
    assert status.status == WorkflowRunStatus.FAILED
    assert status.stage == WorkflowRunStage.FAILED
    assert status.error_code == "WORKFLOW_EXECUTION_FAILED"
    assert status.error_message == "Workflow execution failed."
    assert "secret provider detail" not in (status.error_message or "")


async def test_approve_when_not_pending_raises_conflict(tmp_path: Path) -> None:
    service, _, _, _, _, _ = build_service(tmp_path)
    completed = await start_workflow(service, require_approval=False)

    with pytest.raises(ConflictError):
        await service.approve(completed.thread_id, OWNER_A)


async def test_reject_requires_feedback(tmp_path: Path) -> None:
    service, _, _, _, _, _ = build_service(tmp_path)
    started = await start_workflow(service)

    with pytest.raises(ValidationError):
        await service.reject(started.thread_id, OWNER_A, feedback=" ")


async def test_start_truncates_overlong_topic(tmp_path: Path) -> None:
    service, _, _, _, repository, _ = build_service(tmp_path)
    long_topic = "x" * 1200

    view = await service.start(
        pdf_bytes=b"%PDF-fake",
        filename="sample.pdf",
        owner_key_hash=OWNER_A,
        topic=long_topic,
        require_approval=False,
    )

    assert view.report_topic == "x" * 1000
    run = await repository.get(view.thread_id, OWNER_A)
    assert run is not None
    assert run.report_topic == "x" * 1000


class _BoomOnCreateRepository(InMemoryWorkflowRunRepository):
    async def create(self, run: WorkflowRun) -> WorkflowRun:
        raise RuntimeError("boom")


async def test_start_removes_pdf_when_create_fails(tmp_path: Path) -> None:
    service, _, _, _, _, _ = build_service(
        tmp_path,
        repository=_BoomOnCreateRepository(),
    )
    tid = "00000000-0000-0000-0000-000000000001"

    with pytest.raises(RuntimeError, match="boom"):
        await service.start(
            pdf_bytes=b"%PDF-fake",
            filename="sample.pdf",
            owner_key_hash=OWNER_A,
            topic="General review",
            thread_id=tid,
        )

    pdf_path = tmp_path / "workflows" / tid / "sample.pdf"
    assert not pdf_path.exists()


async def test_double_approve_returns_conflict(tmp_path: Path) -> None:
    service, _, _, _, _, _ = build_service(tmp_path)
    started = await start_workflow(service)

    first = await service.approve(started.thread_id, OWNER_A)
    assert first.status == WorkflowRunStatus.COMPLETED

    with pytest.raises(ConflictError):
        await service.approve(started.thread_id, OWNER_A)
