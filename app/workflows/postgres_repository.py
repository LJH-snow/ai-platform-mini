"""PostgreSQL workflow run repository with API-key tenant isolation."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.workflow_models import WorkflowRunTable
from app.workflows.models import WorkflowRun, WorkflowRunStage, WorkflowRunStatus


class PostgresWorkflowRunRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, run: WorkflowRun) -> WorkflowRun:
        now = datetime.now(UTC)
        row = WorkflowRunTable(
            thread_id=run.thread_id,
            owner_key_hash=run.owner_key_hash,
            status=run.status.value,
            stage=run.stage.value,
            filename=run.filename,
            report_topic=run.report_topic,
            error_code=run.error_code,
            error_message=run.error_message,
            created_at=now,
            updated_at=now,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            return _row_to_run(row)

    async def update(self, run: WorkflowRun) -> WorkflowRun | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(WorkflowRunTable).where(
                    WorkflowRunTable.thread_id == run.thread_id,
                    WorkflowRunTable.owner_key_hash == run.owner_key_hash,
                )
            )
            if row is None:
                return None
            row.status = run.status.value
            row.stage = run.stage.value
            row.filename = run.filename
            row.report_topic = run.report_topic
            row.error_code = run.error_code
            row.error_message = run.error_message
            row.updated_at = datetime.now(UTC)
            await session.commit()
            return _row_to_run(row)

    async def get(self, thread_id: str, owner_key_hash: str) -> WorkflowRun | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(WorkflowRunTable).where(
                    WorkflowRunTable.thread_id == thread_id,
                    WorkflowRunTable.owner_key_hash == owner_key_hash,
                )
            )
            return _row_to_run(row) if row is not None else None

    async def list_by_owner(
        self, owner_key_hash: str, *, limit: int = 20
    ) -> list[WorkflowRun]:
        async with self._session_factory() as session:
            stmt = (
                select(WorkflowRunTable)
                .where(WorkflowRunTable.owner_key_hash == owner_key_hash)
                .order_by(desc(WorkflowRunTable.created_at))
                .limit(limit)
            )
            rows = await session.scalars(stmt)
            return [_row_to_run(row) for row in rows]

    async def update_status_if(
        self,
        thread_id: str,
        owner_key_hash: str,
        *,
        expected_status: WorkflowRunStatus,
        new_status: WorkflowRunStatus,
        new_stage: WorkflowRunStage,
    ) -> WorkflowRun | None:
        async with self._session_factory() as session:
            result = await session.execute(
                update(WorkflowRunTable)
                .where(
                    WorkflowRunTable.thread_id == thread_id,
                    WorkflowRunTable.owner_key_hash == owner_key_hash,
                    WorkflowRunTable.status == expected_status.value,
                )
                .values(
                    status=new_status.value,
                    stage=new_stage.value,
                    updated_at=datetime.now(UTC),
                )
                .returning(WorkflowRunTable)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            await session.commit()
            return _row_to_run(row)


def _row_to_run(row: WorkflowRunTable) -> WorkflowRun:
    return WorkflowRun(
        thread_id=row.thread_id,
        owner_key_hash=row.owner_key_hash,
        status=WorkflowRunStatus(row.status),
        stage=WorkflowRunStage(row.stage),
        filename=row.filename,
        report_topic=row.report_topic,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
