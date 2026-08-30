"""PostgreSQL long-term memory repository with strict owner-scope isolation."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.memory_models import MemoryItemTable
from app.memory.models import MemoryItem


def _normalize_memory_id(memory_id: str) -> str | None:
    try:
        return str(uuid.UUID(memory_id))
    except ValueError:
        return None


class PostgresMemoryRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, item: MemoryItem) -> MemoryItem:
        async with self._session_factory() as session:
            row = MemoryItemTable(
                id=item.id,
                owner_scope=item.owner_scope,
                content=item.content,
                source=item.source.value,
                kind=item.kind.value,
                confidence=item.confidence,
                payload=item.metadata,
                created_at=item.created_at,
                updated_at=item.updated_at,
                last_used_at=item.last_used_at,
            )
            session.add(row)
            await session.commit()
            return _memory_to_domain(row)

    async def get(self, memory_id: str, owner_scope: str) -> MemoryItem | None:
        normalized = _normalize_memory_id(memory_id)
        if normalized is None:
            return None
        async with self._session_factory() as session:
            row = await session.scalar(
                select(MemoryItemTable).where(
                    MemoryItemTable.id == normalized,
                    MemoryItemTable.owner_scope == owner_scope,
                )
            )
            return _memory_to_domain(row) if row is not None else None

    async def list(self, owner_scope: str, limit: int) -> list[MemoryItem]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(MemoryItemTable)
                .where(MemoryItemTable.owner_scope == owner_scope)
                .order_by(
                    MemoryItemTable.last_used_at.desc(),
                    MemoryItemTable.updated_at.desc(),
                    MemoryItemTable.created_at.desc(),
                )
                .limit(limit)
            )
            return [_memory_to_domain(row) for row in rows]

    async def update(self, item: MemoryItem) -> MemoryItem | None:
        current = await self.get(item.id, item.owner_scope)
        if current is None:
            return None
        async with self._session_factory() as session:
            row = await session.scalar(
                select(MemoryItemTable).where(
                    MemoryItemTable.id == _normalize_memory_id(item.id),
                    MemoryItemTable.owner_scope == item.owner_scope,
                )
            )
            if row is None:
                return None
            row.content = item.content
            row.source = item.source.value
            row.kind = item.kind.value
            row.confidence = item.confidence
            row.payload = item.metadata
            row.updated_at = item.updated_at
            await session.commit()
            return _memory_to_domain(row)

    async def delete(self, memory_id: str, owner_scope: str) -> bool:
        current = await self.get(memory_id, owner_scope)
        if current is None:
            return False
        async with self._session_factory() as session:
            row = await session.scalar(
                select(MemoryItemTable).where(
                    MemoryItemTable.id == _normalize_memory_id(memory_id),
                    MemoryItemTable.owner_scope == owner_scope,
                )
            )
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def mark_used(
        self,
        memory_id: str,
        owner_scope: str,
        used_at: datetime,
    ) -> MemoryItem | None:
        current = await self.get(memory_id, owner_scope)
        if current is None:
            return None
        async with self._session_factory() as session:
            row = await session.scalar(
                select(MemoryItemTable).where(
                    MemoryItemTable.id == _normalize_memory_id(memory_id),
                    MemoryItemTable.owner_scope == owner_scope,
                )
            )
            if row is None:
                return None
            row.last_used_at = used_at
            row.updated_at = used_at
            await session.commit()
            return _memory_to_domain(row)


def _memory_to_domain(row: MemoryItemTable) -> MemoryItem:
    from app.memory.models import MemoryKind, MemorySource

    return MemoryItem(
        id=row.id,
        owner_scope=row.owner_scope,
        content=row.content,
        source=MemorySource(row.source),
        kind=MemoryKind(row.kind),
        confidence=row.confidence,
        metadata=dict(row.payload),
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_used_at=row.last_used_at,
    )
