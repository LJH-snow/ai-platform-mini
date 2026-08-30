"""Storage contract for long-term memory items."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.memory.models import MemoryItem


@runtime_checkable
class MemoryRepository(Protocol):
    async def create(self, item: MemoryItem) -> MemoryItem: ...

    async def get(self, memory_id: str, owner_scope: str) -> MemoryItem | None: ...

    async def list(self, owner_scope: str, limit: int) -> list[MemoryItem]: ...

    async def update(self, item: MemoryItem) -> MemoryItem | None: ...

    async def delete(self, memory_id: str, owner_scope: str) -> bool: ...

    async def mark_used(
        self,
        memory_id: str,
        owner_scope: str,
        used_at: datetime,
    ) -> MemoryItem | None: ...
