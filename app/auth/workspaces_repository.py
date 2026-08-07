"""Workspace repository protocol and implementations (memory + postgres)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.user_models import WorkspaceMemberTable, WorkspaceTable
from app.exceptions.base import ConflictError


@dataclass
class WorkspaceRecord:
    id: str
    name: str
    created_by_user_id: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class WorkspaceMemberRecord:
    id: int
    workspace_id: str
    user_id: str
    role: str
    created_at: datetime | None = None


@runtime_checkable
class WorkspaceRepository(Protocol):
    async def create_workspace(self, record: WorkspaceRecord) -> WorkspaceRecord: ...

    async def find_workspace_by_id(
        self, workspace_id: str
    ) -> WorkspaceRecord | None: ...

    async def list_workspaces_for_user(
        self, user_id: str
    ) -> list[tuple[WorkspaceRecord, str]]:
        """Return (workspace, role) pairs for the given user."""
        ...

    async def add_member(
        self, workspace_id: str, user_id: str, role: str
    ) -> WorkspaceMemberRecord: ...

    async def list_members(self, workspace_id: str) -> list[WorkspaceMemberRecord]: ...

    async def get_member(
        self, workspace_id: str, user_id: str
    ) -> WorkspaceMemberRecord | None: ...

    async def update_member_role(
        self, workspace_id: str, user_id: str, role: str
    ) -> bool: ...

    async def remove_member(self, workspace_id: str, user_id: str) -> bool: ...


# ── In-memory implementation ────────────────────────────────────────────────


class InMemoryWorkspaceRepository:
    def __init__(self) -> None:
        self._workspaces: dict[str, WorkspaceRecord] = {}
        self._members: list[WorkspaceMemberRecord] = []
        self._member_id_seq = 0

    async def create_workspace(self, record: WorkspaceRecord) -> WorkspaceRecord:
        self._workspaces[record.id] = record
        return record

    async def find_workspace_by_id(self, workspace_id: str) -> WorkspaceRecord | None:
        return self._workspaces.get(workspace_id)

    async def list_workspaces_for_user(
        self, user_id: str
    ) -> list[tuple[WorkspaceRecord, str]]:
        result: list[tuple[WorkspaceRecord, str]] = []
        for m in self._members:
            if m.user_id == user_id:
                ws = self._workspaces.get(m.workspace_id)
                if ws is not None:
                    result.append((ws, m.role))
        return result

    async def add_member(
        self, workspace_id: str, user_id: str, role: str
    ) -> WorkspaceMemberRecord:
        # Check for duplicate — must match Postgres UNIQUE constraint behaviour
        for m in self._members:
            if m.workspace_id == workspace_id and m.user_id == user_id:
                raise ConflictError(
                    f"User {user_id} is already a member of workspace {workspace_id}"
                )
        self._member_id_seq += 1
        record = WorkspaceMemberRecord(
            id=self._member_id_seq,
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            created_at=datetime.now(UTC),
        )
        self._members.append(record)
        return record

    async def list_members(self, workspace_id: str) -> list[WorkspaceMemberRecord]:
        return [m for m in self._members if m.workspace_id == workspace_id]

    async def get_member(
        self, workspace_id: str, user_id: str
    ) -> WorkspaceMemberRecord | None:
        for m in self._members:
            if m.workspace_id == workspace_id and m.user_id == user_id:
                return m
        return None

    async def update_member_role(
        self, workspace_id: str, user_id: str, role: str
    ) -> bool:
        for m in self._members:
            if m.workspace_id == workspace_id and m.user_id == user_id:
                m.role = role
                return True
        return False

    async def remove_member(self, workspace_id: str, user_id: str) -> bool:
        for i, m in enumerate(self._members):
            if m.workspace_id == workspace_id and m.user_id == user_id:
                self._members.pop(i)
                return True
        return False


# ── Postgres implementation ─────────────────────────────────────────────────


class PostgresWorkspaceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_workspace(self, record: WorkspaceRecord) -> WorkspaceRecord:
        async with self._session_factory() as session:
            row = WorkspaceTable(
                id=record.id,
                name=record.name,
                created_by_user_id=record.created_by_user_id,
            )
            session.add(row)
            await session.commit()
            return _ws_row_to_record(row)

    async def find_workspace_by_id(self, workspace_id: str) -> WorkspaceRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(WorkspaceTable).where(WorkspaceTable.id == workspace_id)
            )
            if row is None:
                return None
            return _ws_row_to_record(row)

    async def list_workspaces_for_user(
        self, user_id: str
    ) -> list[tuple[WorkspaceRecord, str]]:
        async with self._session_factory() as session:
            stmt = (
                select(WorkspaceTable, WorkspaceMemberTable.role)
                .join(
                    WorkspaceMemberTable,
                    WorkspaceMemberTable.workspace_id == WorkspaceTable.id,
                )
                .where(WorkspaceMemberTable.user_id == user_id)
            )
            result = await session.execute(stmt)
            rows = result.all()
            return [
                (_ws_row_to_record(row[0]), row[1])  # type: ignore[index]
                for row in rows
            ]

    async def add_member(
        self, workspace_id: str, user_id: str, role: str
    ) -> WorkspaceMemberRecord:
        async with self._session_factory() as session:
            row = WorkspaceMemberTable(
                workspace_id=workspace_id,
                user_id=user_id,
                role=role,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _member_row_to_record(row)

    async def list_members(self, workspace_id: str) -> list[WorkspaceMemberRecord]:
        async with self._session_factory() as session:
            stmt = select(WorkspaceMemberTable).where(
                WorkspaceMemberTable.workspace_id == workspace_id
            )
            result = await session.scalars(stmt)
            return [_member_row_to_record(row) for row in result]

    async def get_member(
        self, workspace_id: str, user_id: str
    ) -> WorkspaceMemberRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(WorkspaceMemberTable).where(
                    WorkspaceMemberTable.workspace_id == workspace_id,
                    WorkspaceMemberTable.user_id == user_id,
                )
            )
            if row is None:
                return None
            return _member_row_to_record(row)

    async def update_member_role(
        self, workspace_id: str, user_id: str, role: str
    ) -> bool:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(WorkspaceMemberTable).where(
                    WorkspaceMemberTable.workspace_id == workspace_id,
                    WorkspaceMemberTable.user_id == user_id,
                )
            )
            if row is None:
                return False
            row.role = role
            await session.commit()
            return True

    async def remove_member(self, workspace_id: str, user_id: str) -> bool:
        async with self._session_factory() as session:
            stmt = delete(WorkspaceMemberTable).where(
                WorkspaceMemberTable.workspace_id == workspace_id,
                WorkspaceMemberTable.user_id == user_id,
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0  # type: ignore[attr-defined,no-any-return]


def _ws_row_to_record(row: WorkspaceTable) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=row.id,
        name=row.name,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _member_row_to_record(row: WorkspaceMemberTable) -> WorkspaceMemberRecord:
    return WorkspaceMemberRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        role=row.role,
        created_at=row.created_at,
    )
