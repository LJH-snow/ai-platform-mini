"""User repository protocol and implementations (memory + postgres)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.user_models import UserTable


@dataclass
class UserRecord:
    id: str
    email: str
    display_name: str
    password_salt: str
    password_hash: str
    status: str = "active"
    created_at: datetime | None = None
    updated_at: datetime | None = None


@runtime_checkable
class UserRepository(Protocol):
    async def create(self, record: UserRecord) -> UserRecord: ...

    async def find_by_id(self, user_id: str) -> UserRecord | None: ...

    async def find_by_email(self, email: str) -> UserRecord | None: ...


# ── In-memory implementation ────────────────────────────────────────────────


class InMemoryUserRepository:
    def __init__(self) -> None:
        self._records: dict[str, UserRecord] = {}
        self._by_email: dict[str, str] = {}  # email → user_id

    async def create(self, record: UserRecord) -> UserRecord:
        self._records[record.id] = record
        self._by_email[record.email] = record.id
        return record

    async def find_by_id(self, user_id: str) -> UserRecord | None:
        return self._records.get(user_id)

    async def find_by_email(self, email: str) -> UserRecord | None:
        user_id = self._by_email.get(email)
        if user_id is None:
            return None
        return self._records.get(user_id)


# ── Postgres implementation ─────────────────────────────────────────────────


class PostgresUserRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, record: UserRecord) -> UserRecord:
        async with self._session_factory() as session:
            row = UserTable(
                id=record.id,
                email=record.email,
                display_name=record.display_name,
                password_salt=record.password_salt,
                password_hash=record.password_hash,
                status=record.status,
            )
            session.add(row)
            await session.commit()
            return _user_row_to_record(row)

    async def find_by_id(self, user_id: str) -> UserRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(select(UserTable).where(UserTable.id == user_id))
            if row is None:
                return None
            return _user_row_to_record(row)

    async def find_by_email(self, email: str) -> UserRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(UserTable).where(UserTable.email == email)
            )
            if row is None:
                return None
            return _user_row_to_record(row)


def _user_row_to_record(row: UserTable) -> UserRecord:
    return UserRecord(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        password_salt=row.password_salt,
        password_hash=row.password_hash,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
