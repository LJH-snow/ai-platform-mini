"""HTTP API and PostgreSQL integration tests for the workflow endpoints."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.api.workflows import get_workflow_service
from app.services.workflow_service import PDFReportWorkflowService
from app.workflows.checkpointer import PostgresWorkflowCheckpointer
from app.workflows.memory_repository import InMemoryWorkflowRunRepository
from app.workflows.models import WorkflowRunStatus
from app.workflows.pdf_report import PDFReportWorkflow
from app.workflows.postgres_repository import PostgresWorkflowRunRepository
from app.workflows.repository import WorkflowRunRepository
from app.workflows.serde import create_workflow_serde
from workflow_fakes import (
    FakeModel,
    FakePdfExtractor,
    FakeRetriever,
    make_reference,
)

TEST_KEY = "sk-test-integration"
OWNER_A = "a" * 64
OWNER_B = "b" * 64


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_KEY}"}


def build_api_service(
    tmp_path: Path,
) -> tuple[
    PDFReportWorkflowService,
    FakePdfExtractor,
    FakeRetriever,
    FakeModel,
]:
    extractor = FakePdfExtractor()
    retriever = FakeRetriever(references=(make_reference(),))
    model = FakeModel()
    checkpointer = InMemorySaver(serde=create_workflow_serde())
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
        run_repository=InMemoryWorkflowRunRepository(),
        work_dir=tmp_path / "workflows",
    )
    return service, extractor, retriever, model


@pytest.fixture()
def workflow_client(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, PDFReportWorkflowService, FakeModel]]:
    from app.main import app

    service, _, _, model = build_api_service(tmp_path)
    app.dependency_overrides[get_workflow_service] = lambda: service
    with TestClient(app) as client:
        yield client, service, model
    app.dependency_overrides.pop(get_workflow_service, None)


def _upload_pdf(
    client: TestClient,
    *,
    topic: str | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = {"topic": topic} if topic is not None else {}
    response = client.post(
        "/api/v1/workflows/pdf-report",
        headers=headers or _auth(),
        files={"file": ("sample.pdf", b"%PDF-fake", "application/pdf")},
        data=data,
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def test_upload_pauses_for_approval_and_get_returns_status(
    workflow_client: tuple[TestClient, PDFReportWorkflowService, FakeModel],
) -> None:
    client, _, _ = workflow_client

    body = _upload_pdf(client, topic="Quarterly review")

    assert body["status"] == "pending_approval"
    assert body["stage"] == "awaiting_approval"
    assert body["draft_summary"] == "Fake analysis"
    assert body["filename"] == "sample.pdf"

    fetched = client.get(
        f"/api/v1/workflows/{body['thread_id']}",
        headers=_auth(),
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "pending_approval"
    assert fetched.json()["report_topic"] == "Quarterly review"


def test_approve_generates_report(
    workflow_client: tuple[TestClient, PDFReportWorkflowService, FakeModel],
) -> None:
    client, _, _ = workflow_client
    started = _upload_pdf(client)

    response = client.post(
        f"/api/v1/workflows/{started['thread_id']}/approve",
        headers=_auth(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["stage"] == "completed"
    assert "Fake analysis" in (body["report"] or "")


def test_reject_reanalyzes_with_feedback(
    workflow_client: tuple[TestClient, PDFReportWorkflowService, FakeModel],
) -> None:
    client, _, model = workflow_client
    started = _upload_pdf(client)

    response = client.post(
        f"/api/v1/workflows/{started['thread_id']}/reject",
        headers=_auth(),
        json={"feedback": "add risk section"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["stage"] == "awaiting_approval"
    assert len(model.messages) == 2


def test_missing_auth_returns_401(
    workflow_client: tuple[TestClient, PDFReportWorkflowService, FakeModel],
) -> None:
    client, _, _ = workflow_client

    response = client.post(
        "/api/v1/workflows/pdf-report",
        files={"file": ("sample.pdf", b"%PDF-fake", "application/pdf")},
    )

    assert response.status_code == 401


def test_invalid_thread_id_returns_404(
    workflow_client: tuple[TestClient, PDFReportWorkflowService, FakeModel],
) -> None:
    client, _, _ = workflow_client

    response = client.get("/api/v1/workflows/not-a-uuid", headers=_auth())

    assert response.status_code == 404
    assert response.json()["code"] == "WORKFLOW_NOT_FOUND"


async def test_cross_tenant_access_returns_404(
    workflow_client: tuple[TestClient, PDFReportWorkflowService, FakeModel],
) -> None:
    client, service, _ = workflow_client
    started = await service.start(
        pdf_bytes=b"%PDF-fake",
        filename="private.pdf",
        owner_key_hash=OWNER_B,
        topic="Private",
    )
    thread_id = started.thread_id

    fetched = client.get(f"/api/v1/workflows/{thread_id}", headers=_auth())
    approved = client.post(
        f"/api/v1/workflows/{thread_id}/approve",
        headers=_auth(),
    )

    assert fetched.status_code == 404
    assert approved.status_code == 404


def test_reject_requires_feedback(
    workflow_client: tuple[TestClient, PDFReportWorkflowService, FakeModel],
) -> None:
    client, _, _ = workflow_client
    started = _upload_pdf(client)

    response = client.post(
        f"/api/v1/workflows/{started['thread_id']}/reject",
        headers=_auth(),
        json={},
    )

    assert response.status_code == 422


@pytest.mark.skipif(
    not os.getenv("INTEGRATION_TEST"),
    reason="Set INTEGRATION_TEST=1 to run PostgreSQL workflow integration tests",
)
class TestWorkflowPostgresIntegration:
    @pytest.fixture()
    async def postgres_store(
        self, tmp_path: Path
    ) -> AsyncGenerator[
        tuple[
            PostgresWorkflowRunRepository,
            PostgresWorkflowCheckpointer,
            str,
            Path,
        ],
        None,
    ]:
        from testcontainers.community.postgres import PostgresContainer

        from app.db.init import dispose_db, init_db
        from app.db.session import create_async_session_factory

        with PostgresContainer("pgvector/pgvector:pg16") as pg:
            database_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
            await init_db(database_url, include_rag=False)
            repository = PostgresWorkflowRunRepository(create_async_session_factory())
            checkpointer = PostgresWorkflowCheckpointer(database_url)
            await checkpointer.open()
            yield repository, checkpointer, database_url, tmp_path
            await checkpointer.close()
            await dispose_db()

    @pytest.mark.asyncio
    async def test_checkpoint_resumes_after_restart(
        self,
        postgres_store: tuple[
            PostgresWorkflowRunRepository,
            PostgresWorkflowCheckpointer,
            str,
            Path,
        ],
    ) -> None:
        repository, checkpointer, database_url, tmp_path = postgres_store
        first = _build_postgres_service(
            tmp_path,
            checkpointer.saver,
            repository,
        )
        started = await first.start(
            pdf_bytes=b"%PDF-fake",
            filename="sample.pdf",
            owner_key_hash=OWNER_A,
            topic="Postgres resume",
        )
        assert started.status == WorkflowRunStatus.PENDING_APPROVAL

        await checkpointer.close()
        restarted_checkpointer = PostgresWorkflowCheckpointer(database_url)
        await restarted_checkpointer.open()
        restarted = _build_postgres_service(
            tmp_path,
            restarted_checkpointer.saver,
            repository,
        )

        completed = await restarted.approve(started.thread_id, OWNER_A)

        assert completed.status == WorkflowRunStatus.COMPLETED
        await restarted_checkpointer.close()


def _build_postgres_service(
    tmp_path: Path,
    checkpointer: BaseCheckpointSaver,
    repository: WorkflowRunRepository,
) -> PDFReportWorkflowService:
    workflow = PDFReportWorkflow(
        extractor=FakePdfExtractor(),
        retriever=FakeRetriever(references=(make_reference(),)),
        model=FakeModel(),
        checkpointer=checkpointer,
        max_document_characters=1_000,
        max_reference_characters=500,
    )
    return PDFReportWorkflowService(
        workflow=workflow,
        checkpointer=checkpointer,
        run_repository=repository,
        work_dir=tmp_path / "pg-workflows",
    )
