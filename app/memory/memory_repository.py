"""In-memory long-term memory repository for local development and tests."""

from dataclasses import replace
from datetime import UTC, datetime

from app.memory.models import MemoryItem


class InMemoryMemoryRepository:
    def __init__(self) -> None:
        self._items: dict[str, MemoryItem] = {}

    async def create(self, item: MemoryItem) -> MemoryItem:
        self._items[item.id] = item
        return item

    async def get(self, memory_id: str, owner_scope: str) -> MemoryItem | None:
        item = self._items.get(memory_id)
        if item is None or item.owner_scope != owner_scope:
            return None
        return item

    async def list(self, owner_scope: str, limit: int) -> list[MemoryItem]:
        items = sorted(
            (item for item in self._items.values() if item.owner_scope == owner_scope),
            key=_memory_order_key,
            reverse=True,
        )
        return items[:limit]

    async def update(self, item: MemoryItem) -> MemoryItem | None:
        current = await self.get(item.id, item.owner_scope)
        if current is None:
            return None
        updated = replace(
            current,
            content=item.content,
            source=item.source,
            kind=item.kind,
            confidence=item.confidence,
            metadata=item.metadata,
            updated_at=item.updated_at,
        )
        self._items[item.id] = updated
        return updated

    async def delete(self, memory_id: str, owner_scope: str) -> bool:
        item = await self.get(memory_id, owner_scope)
        if item is None:
            return False
        self._items.pop(memory_id, None)
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
        updated = replace(current, last_used_at=used_at)
        self._items[current.id] = updated
        return updated


def _memory_order_key(item: MemoryItem) -> datetime:
    return (
        item.last_used_at
        or item.updated_at
        or item.created_at
        or datetime.min.replace(tzinfo=UTC)
    )
