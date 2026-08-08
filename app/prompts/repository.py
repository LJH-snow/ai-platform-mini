"""Prompt Registry repository protocol and implementations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.prompt_models import PromptTemplateTable
from app.prompts.models import PromptRecord


@runtime_checkable
class PromptRepository(Protocol):
    async def create_version(self, record: PromptRecord) -> PromptRecord: ...
    async def find_active(
        self, workspace_id: str | None, name: str
    ) -> PromptRecord | None: ...
    async def find_by_name_and_version(
        self, workspace_id: str | None, name: str, version: int
    ) -> PromptRecord | None: ...
    async def list_versions(
        self, workspace_id: str | None, name: str
    ) -> list[PromptRecord]: ...
    async def set_active(
        self, workspace_id: str | None, name: str, version: int
    ) -> bool: ...
    async def list_active_templates(
        self, workspace_id: str | None
    ) -> list[PromptRecord]: ...
    async def seed(self, record: PromptRecord) -> PromptRecord: ...


# ── In-memory ────────────────────────────────────────────────────────────────


class InMemoryPromptRepository:
    def __init__(self) -> None:
        self._records: list[PromptRecord] = []
        self._next_id = 0

    async def create_version(self, record: PromptRecord) -> PromptRecord:
        self._next_id += 1
        record.id = self._next_id
        self._records.append(record)
        return record

    async def find_active(
        self, workspace_id: str | None, name: str
    ) -> PromptRecord | None:
        for r in self._records:
            if r.workspace_id == workspace_id and r.name == name and r.is_active:
                return r
        return None

    async def find_by_name_and_version(
        self, workspace_id: str | None, name: str, version: int
    ) -> PromptRecord | None:
        for r in self._records:
            if (
                r.workspace_id == workspace_id
                and r.name == name
                and r.version == version
            ):
                return r
        return None

    async def list_versions(
        self, workspace_id: str | None, name: str
    ) -> list[PromptRecord]:
        return [
            r
            for r in self._records
            if r.workspace_id == workspace_id and r.name == name
        ]

    async def set_active(
        self, workspace_id: str | None, name: str, version: int
    ) -> bool:
        target = await self.find_by_name_and_version(workspace_id, name, version)
        if target is None:
            return False
        for r in self._records:
            if r.workspace_id == workspace_id and r.name == name:
                r.is_active = False
        target.is_active = True
        return True

    async def list_active_templates(
        self, workspace_id: str | None
    ) -> list[PromptRecord]:
        """Workspace-first with global fallback (same semantics as render).

        A workspace template wins over the global one for the same name;
        global (NULL) templates are listed when the workspace has none.
        """
        candidates = [
            r
            for r in self._records
            if r.is_active
            and (r.workspace_id == workspace_id or r.workspace_id is None)
        ]
        by_name: dict[str, PromptRecord] = {}
        for record in candidates:
            current = by_name.get(record.name)
            if current is None or record.workspace_id == workspace_id:
                by_name[record.name] = record
        return list(by_name.values())

    async def seed(self, record: PromptRecord) -> PromptRecord:
        existing = await self.find_by_name_and_version(
            record.workspace_id, record.name, record.version
        )
        if existing is not None:
            return existing
        return await self.create_version(record)


# ── Postgres ─────────────────────────────────────────────────────────────────


class PostgresPromptRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_version(self, record: PromptRecord) -> PromptRecord:
        async with self._session_factory() as session:
            row = PromptTemplateTable(
                workspace_id=record.workspace_id,
                name=record.name,
                version=record.version,
                content=record.content,
                variables=record.variables,
                is_active=record.is_active,
                created_by=record.created_by,
            )
            session.add(row)
            await session.commit()
            return _row_to_record(row)

    async def find_active(
        self, workspace_id: str | None, name: str
    ) -> PromptRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(PromptTemplateTable).where(
                    PromptTemplateTable.workspace_id == workspace_id,
                    PromptTemplateTable.name == name,
                    PromptTemplateTable.is_active.is_(True),
                )
            )
            return _row_to_record(row) if row else None

    async def find_by_name_and_version(
        self, workspace_id: str | None, name: str, version: int
    ) -> PromptRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(PromptTemplateTable).where(
                    PromptTemplateTable.workspace_id == workspace_id,
                    PromptTemplateTable.name == name,
                    PromptTemplateTable.version == version,
                )
            )
            return _row_to_record(row) if row else None

    async def list_versions(
        self, workspace_id: str | None, name: str
    ) -> list[PromptRecord]:
        async with self._session_factory() as session:
            stmt = (
                select(PromptTemplateTable)
                .where(
                    PromptTemplateTable.workspace_id == workspace_id,
                    PromptTemplateTable.name == name,
                )
                .order_by(PromptTemplateTable.version.desc())
            )
            result = await session.scalars(stmt)
            return [_row_to_record(row) for row in result]

    async def set_active(
        self, workspace_id: str | None, name: str, version: int
    ) -> bool:
        async with self._session_factory() as session:
            target = await session.scalar(
                select(PromptTemplateTable).where(
                    PromptTemplateTable.workspace_id == workspace_id,
                    PromptTemplateTable.name == name,
                    PromptTemplateTable.version == version,
                )
            )
            if target is None:
                return False
            await session.execute(
                update(PromptTemplateTable)
                .where(
                    PromptTemplateTable.workspace_id == workspace_id,
                    PromptTemplateTable.name == name,
                )
                .values(is_active=False)
            )
            target.is_active = True
            await session.commit()
            return True

    async def list_active_templates(
        self, workspace_id: str | None
    ) -> list[PromptRecord]:
        """Workspace-first with global fallback (same semantics as render)."""
        async with self._session_factory() as session:
            stmt = select(PromptTemplateTable).where(
                or_(
                    PromptTemplateTable.workspace_id == workspace_id,
                    PromptTemplateTable.workspace_id.is_(None),
                ),
                PromptTemplateTable.is_active.is_(True),
            )
            rows = await session.scalars(stmt)
            by_name: dict[str, PromptRecord] = {}
            for row in rows:
                record = _row_to_record(row)
                current = by_name.get(record.name)
                if current is None or record.workspace_id == workspace_id:
                    by_name[record.name] = record
            return list(by_name.values())

    async def seed(self, record: PromptRecord) -> PromptRecord:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(PromptTemplateTable).where(
                    PromptTemplateTable.workspace_id == record.workspace_id,
                    PromptTemplateTable.name == record.name,
                    PromptTemplateTable.version == record.version,
                )
            )
            if existing is not None:
                return _row_to_record(existing)
            row = PromptTemplateTable(
                workspace_id=record.workspace_id,
                name=record.name,
                version=record.version,
                content=record.content,
                variables=record.variables,
                is_active=record.is_active,
                created_by=record.created_by,
            )
            session.add(row)
            await session.commit()
            return _row_to_record(row)


def _row_to_record(row: PromptTemplateTable) -> PromptRecord:
    return PromptRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        version=row.version,
        content=row.content,
        variables=row.variables if isinstance(row.variables, list) else [],
        is_active=row.is_active,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
