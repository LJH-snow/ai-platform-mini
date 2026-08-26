"""Workflow builder repository protocols and dual (InMemory/Postgres) storage.

NOTE: ``WorkflowRunRepository`` here is a different boundary than the
same-named protocol in ``app/workflows/repository.py`` (the fixed PDF
report flow). The PDF flow persists ``workflow_runs`` rows keyed by
thread_id; this package persists ``workflow_builder_runs`` rows keyed by
run UUID with a full definition snapshot. Import paths keep them apart.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, cast, runtime_checkable

from sqlalchemy import CursorResult, delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.workflow_builder_models import WorkflowBuilderRunTable, WorkflowTable
from app.workflow_builder.models import WorkflowRecord, WorkflowRunRecord

_MIN_DATETIME = datetime.min


@runtime_checkable
class WorkflowRepository(Protocol):
    """Persistence boundary for workflow definitions."""

    async def create_workflow(self, record: WorkflowRecord) -> WorkflowRecord: ...
    async def get_workflow(
        self, workflow_id: str, workspace_id: str | None = None
    ) -> WorkflowRecord | None: ...
    async def list_workflows(self, workspace_id: str) -> list[WorkflowRecord]: ...
    async def update_workflow(
        self, record: WorkflowRecord
    ) -> WorkflowRecord | None: ...
    async def publish_workflow(
        self, record: WorkflowRecord
    ) -> WorkflowRecord | None: ...
    async def delete_workflow(self, workflow_id: str, workspace_id: str) -> bool: ...


@runtime_checkable
class WorkflowRunRepository(Protocol):
    """Persistence boundary for workflow runs (builder, not PDF flow)."""

    async def create_run(self, record: WorkflowRunRecord) -> WorkflowRunRecord: ...
    async def update_run(
        self, record: WorkflowRunRecord
    ) -> WorkflowRunRecord | None: ...
    async def get_run(
        self, run_id: str, workspace_id: str | None = None
    ) -> WorkflowRunRecord | None: ...
    async def list_runs(
        self, workflow_id: str, limit: int
    ) -> list[WorkflowRunRecord]: ...


# ── In-memory ────────────────────────────────────────────────────────────────


class InMemoryWorkflowRepository:
    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowRecord] = {}

    async def create_workflow(self, record: WorkflowRecord) -> WorkflowRecord:
        self._workflows[record.id] = record
        return record

    async def get_workflow(
        self, workflow_id: str, workspace_id: str | None = None
    ) -> WorkflowRecord | None:
        record = self._workflows.get(workflow_id)
        if record is None:
            return None
        if workspace_id is not None and record.workspace_id != workspace_id:
            return None
        return record

    async def list_workflows(self, workspace_id: str) -> list[WorkflowRecord]:
        records = [
            record
            for record in self._workflows.values()
            if record.workspace_id == workspace_id
        ]
        records.sort(
            key=lambda record: record.created_at or _MIN_DATETIME, reverse=True
        )
        return records

    async def update_workflow(self, record: WorkflowRecord) -> WorkflowRecord | None:
        current = self._workflows.get(record.id)
        if current is None or current.workspace_id != record.workspace_id:
            return None
        self._workflows[record.id] = record
        return record

    async def publish_workflow(self, record: WorkflowRecord) -> WorkflowRecord | None:
        return await self.update_workflow(record)

    async def delete_workflow(self, workflow_id: str, workspace_id: str) -> bool:
        current = self._workflows.get(workflow_id)
        if current is None or current.workspace_id != workspace_id:
            return False
        del self._workflows[workflow_id]
        return True


class InMemoryWorkflowRunRepository:
    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRunRecord] = {}

    async def create_run(self, record: WorkflowRunRecord) -> WorkflowRunRecord:
        self._runs[record.id] = record
        return record

    async def update_run(self, record: WorkflowRunRecord) -> WorkflowRunRecord | None:
        current = self._runs.get(record.id)
        if current is None:
            return None
        self._runs[record.id] = record
        return record

    async def get_run(
        self, run_id: str, workspace_id: str | None = None
    ) -> WorkflowRunRecord | None:
        record = self._runs.get(run_id)
        if record is None:
            return None
        if workspace_id is not None and record.workspace_id != workspace_id:
            return None
        return record

    async def list_runs(self, workflow_id: str, limit: int) -> list[WorkflowRunRecord]:
        records = [
            record
            for record in self._runs.values()
            if record.workflow_id == workflow_id
        ]
        records.sort(
            key=lambda record: record.created_at or _MIN_DATETIME, reverse=True
        )
        return records[:limit]


# ── Postgres ─────────────────────────────────────────────────────────────────


class PostgresWorkflowRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_workflow(self, record: WorkflowRecord) -> WorkflowRecord:
        async with self._session_factory() as session:
            row = WorkflowTable(
                id=record.id,
                workspace_id=record.workspace_id,
                name=record.name,
                description=record.description,
                status=record.status,
                definition=record.definition,
                version=record.version,
                created_by=record.created_by,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _workflow_row_to_record(row)

    async def get_workflow(
        self, workflow_id: str, workspace_id: str | None = None
    ) -> WorkflowRecord | None:
        stmt = select(WorkflowTable).where(WorkflowTable.id == workflow_id)
        if workspace_id is not None:
            stmt = stmt.where(WorkflowTable.workspace_id == workspace_id)
        async with self._session_factory() as session:
            row = await session.scalar(stmt)
            if row is None:
                return None
            return _workflow_row_to_record(row)

    async def list_workflows(self, workspace_id: str) -> list[WorkflowRecord]:
        stmt = (
            select(WorkflowTable)
            .where(WorkflowTable.workspace_id == workspace_id)
            .order_by(desc(WorkflowTable.created_at))
        )
        async with self._session_factory() as session:
            rows = await session.scalars(stmt)
            return [_workflow_row_to_record(row) for row in rows]

    async def update_workflow(self, record: WorkflowRecord) -> WorkflowRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(WorkflowTable).where(
                    WorkflowTable.id == record.id,
                    WorkflowTable.workspace_id == record.workspace_id,
                )
            )
            if row is None:
                return None
            row.name = record.name
            row.description = record.description
            row.definition = record.definition
            row.updated_at = record.updated_at
            await session.commit()
            await session.refresh(row)
            return _workflow_row_to_record(row)

    async def publish_workflow(self, record: WorkflowRecord) -> WorkflowRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(WorkflowTable).where(
                    WorkflowTable.id == record.id,
                    WorkflowTable.workspace_id == record.workspace_id,
                )
            )
            if row is None:
                return None
            row.status = record.status
            row.definition = record.definition
            row.version = record.version
            row.updated_at = record.updated_at
            await session.commit()
            await session.refresh(row)
            return _workflow_row_to_record(row)

    async def delete_workflow(self, workflow_id: str, workspace_id: str) -> bool:
        async with self._session_factory() as session:
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    delete(WorkflowTable).where(
                        WorkflowTable.id == workflow_id,
                        WorkflowTable.workspace_id == workspace_id,
                    )
                ),
            )
            await session.commit()
            return result.rowcount > 0


class PostgresWorkflowRunRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_run(self, record: WorkflowRunRecord) -> WorkflowRunRecord:
        async with self._session_factory() as session:
            row = WorkflowBuilderRunTable(
                id=record.id,
                workflow_id=record.workflow_id,
                workspace_id=record.workspace_id,
                status=record.status,
                inputs=record.inputs,
                definition=record.definition,
                node_results=record.node_results,
                error=record.error,
                total_duration_ms=record.total_duration_ms,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _run_row_to_record(row)

    async def update_run(self, record: WorkflowRunRecord) -> WorkflowRunRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(WorkflowBuilderRunTable).where(
                    WorkflowBuilderRunTable.id == record.id
                )
            )
            if row is None:
                return None
            row.status = record.status
            row.node_results = record.node_results
            row.error = record.error
            row.total_duration_ms = record.total_duration_ms
            row.completed_at = record.completed_at
            await session.commit()
            await session.refresh(row)
            return _run_row_to_record(row)

    async def get_run(
        self, run_id: str, workspace_id: str | None = None
    ) -> WorkflowRunRecord | None:
        stmt = select(WorkflowBuilderRunTable).where(
            WorkflowBuilderRunTable.id == run_id
        )
        if workspace_id is not None:
            stmt = stmt.where(WorkflowBuilderRunTable.workspace_id == workspace_id)
        async with self._session_factory() as session:
            row = await session.scalar(stmt)
            if row is None:
                return None
            return _run_row_to_record(row)

    async def list_runs(self, workflow_id: str, limit: int) -> list[WorkflowRunRecord]:
        stmt = (
            select(WorkflowBuilderRunTable)
            .where(WorkflowBuilderRunTable.workflow_id == workflow_id)
            .order_by(desc(WorkflowBuilderRunTable.created_at))
            .limit(limit)
        )
        async with self._session_factory() as session:
            rows = await session.scalars(stmt)
            return [_run_row_to_record(row) for row in rows]


def _workflow_row_to_record(row: WorkflowTable) -> WorkflowRecord:
    return WorkflowRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        description=row.description,
        status=row.status,
        definition=dict(row.definition),
        version=row.version,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run_row_to_record(row: WorkflowBuilderRunTable) -> WorkflowRunRecord:
    return WorkflowRunRecord(
        id=row.id,
        workflow_id=row.workflow_id,
        workspace_id=row.workspace_id,
        status=row.status,
        inputs=dict(row.inputs),
        definition=dict(row.definition),
        node_results=list(row.node_results),
        error=row.error,
        total_duration_ms=row.total_duration_ms,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )
