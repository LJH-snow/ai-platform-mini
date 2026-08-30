"""Application service for tenant-scoped long-term memory."""

import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from app.exceptions.base import MemoryNotFoundError, ValidationError
from app.memory.memory_repository import InMemoryMemoryRepository
from app.memory.models import MemoryItem, MemoryKind, MemorySource, validate_owner_scope
from app.memory.repository import MemoryRepository

_MAX_CONTENT_LENGTH = 10_000
_MAX_METADATA_KEYS = 32
_MAX_METADATA_KEY_LENGTH = 64
_MAX_METADATA_VALUE_LENGTH = 500
_DEFAULT_LIST_LIMIT = 20
_MAX_LIST_LIMIT = 100
_MAX_FETCH_FOR_SEARCH = 200
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


class MemoryService:
    """Owns validation, relevance selection, and usage bookkeeping."""

    def __init__(
        self,
        repository: MemoryRepository,
        *,
        context_limit: int = 5,
        context_max_chars: int = 3000,
    ) -> None:
        self._repository = repository
        self._context_limit = max(context_limit, 0)
        self._context_max_chars = max(context_max_chars, 100)

    async def create_memory(
        self,
        owner_scope: str,
        content: str,
        *,
        source: MemorySource = MemorySource.EXPLICIT,
        kind: MemoryKind = MemoryKind.FACT,
        confidence: float = 1.0,
        metadata: Mapping[str, object] | None = None,
    ) -> MemoryItem:
        owner = validate_owner_scope(owner_scope)
        normalized = self._normalize_content(content)
        normalized_metadata = self._normalize_metadata(metadata or {})
        now = datetime.now(UTC)
        item = MemoryItem(
            id=str(uuid.uuid4()),
            owner_scope=owner,
            content=normalized,
            source=source,
            kind=kind,
            confidence=confidence,
            metadata=normalized_metadata,
            created_at=now,
            updated_at=now,
        )
        self._validate_confidence(item.confidence)
        return await self._repository.create(item)

    async def get_memory(self, owner_scope: str, memory_id: str) -> MemoryItem:
        owner = validate_owner_scope(owner_scope)
        item = await self._repository.get(memory_id, owner)
        if item is None:
            raise MemoryNotFoundError("Memory item not found.")
        return item

    async def list_memories(
        self,
        owner_scope: str,
        *,
        limit: int = _DEFAULT_LIST_LIMIT,
        query: str | None = None,
    ) -> list[MemoryItem]:
        owner = validate_owner_scope(owner_scope)
        normalized_limit = max(min(limit, _MAX_LIST_LIMIT), 1)
        query = query.strip() if query else ""
        if not query:
            return await self._repository.list(owner, normalized_limit)

        raw = await self._repository.list(owner, _MAX_FETCH_FOR_SEARCH)
        query_terms = _text_terms(query)
        if not query_terms:
            return raw[:normalized_limit]
        ranked = sorted(
            raw,
            key=lambda item: (_overlap_score(item, query_terms), _recent_key(item)),
            reverse=True,
        )
        return [item for item in ranked if _overlap_score(item, query_terms) > 0][
            :normalized_limit
        ]

    async def update_memory(
        self,
        owner_scope: str,
        memory_id: str,
        *,
        content: str | None = None,
        source: MemorySource | None = None,
        kind: MemoryKind | None = None,
        confidence: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> MemoryItem:
        owner = validate_owner_scope(owner_scope)
        current = await self._repository.get(memory_id, owner)
        if current is None:
            raise MemoryNotFoundError("Memory item not found.")
        if (
            content is None
            and source is None
            and kind is None
            and confidence is None
            and metadata is None
        ):
            raise ValidationError("At least one memory field must be updated.")

        updated_content = (
            self._normalize_content(content) if content is not None else current.content
        )
        updated_confidence = (
            confidence if confidence is not None else current.confidence
        )
        self._validate_confidence(updated_confidence)
        updated_metadata = (
            self._normalize_metadata(metadata)
            if metadata is not None
            else current.metadata
        )
        updated = MemoryItem(
            id=current.id,
            owner_scope=current.owner_scope,
            content=updated_content,
            source=source or current.source,
            kind=kind or current.kind,
            confidence=updated_confidence,
            metadata=updated_metadata,
            created_at=current.created_at,
            updated_at=datetime.now(UTC),
            last_used_at=current.last_used_at,
        )
        result = await self._repository.update(updated)
        if result is None:
            raise MemoryNotFoundError("Memory item not found.")
        return result

    async def delete_memory(self, owner_scope: str, memory_id: str) -> None:
        owner = validate_owner_scope(owner_scope)
        deleted = await self._repository.delete(memory_id, owner)
        if not deleted:
            raise MemoryNotFoundError("Memory item not found.")

    async def retrieve_for_agent(
        self,
        owner_scope: str,
        query: str,
        *,
        limit: int | None = None,
        max_chars: int | None = None,
    ) -> Sequence[MemoryItem]:
        """Return relevant memory for one Agent context preparation step."""

        if self._context_limit <= 0:
            return ()
        selected = await self.list_memories(
            owner_scope,
            limit=self._context_limit if limit is None else limit,
            query=query,
        )
        if not selected:
            return ()
        if max_chars is None:
            max_chars = self._context_max_chars
        bounded: list[MemoryItem] = []
        remaining = max_chars
        for item in selected:
            if remaining <= 0:
                break
            content = item.content[:remaining]
            bounded.append(item)
            remaining -= len(content) + 32
        for item in bounded:
            await self._repository.mark_used(item.id, owner_scope, datetime.now(UTC))
        return bounded

    @staticmethod
    def _normalize_content(content: str) -> str:
        normalized = content.strip()
        if not normalized:
            raise ValidationError("content must not be empty")
        if len(normalized) > _MAX_CONTENT_LENGTH:
            raise ValidationError(
                f"content must be at most {_MAX_CONTENT_LENGTH} characters"
            )
        return normalized

    @staticmethod
    def _validate_confidence(confidence: float) -> None:
        if not 0.0 <= confidence <= 1.0:
            raise ValidationError("confidence must be between 0 and 1")

    @staticmethod
    def _normalize_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
        if len(metadata) > _MAX_METADATA_KEYS:
            raise ValidationError(
                f"metadata must have at most {_MAX_METADATA_KEYS} keys"
            )
        normalized: dict[str, object] = {}
        for key, value in metadata.items():
            if not key or len(key) > _MAX_METADATA_KEY_LENGTH:
                raise ValidationError(
                    f"metadata key must be between 1 and {_MAX_METADATA_KEY_LENGTH} "
                    "characters"
                )
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                raise ValidationError("metadata values must be JSON scalar values")
            if isinstance(value, str) and len(value) > _MAX_METADATA_VALUE_LENGTH:
                raise ValidationError(
                    f"metadata string values must be at most "
                    f"{_MAX_METADATA_VALUE_LENGTH} characters"
                )
            normalized[key] = value
        return normalized


def _text_terms(text: str) -> set[str]:
    lowered = text.lower()
    terms: set[str] = set()
    for token in _ASCII_TOKEN_RE.findall(lowered):
        if len(token) >= 2:
            terms.add(token)
    for run in _CJK_RUN_RE.findall(lowered):
        if len(run) <= 8:
            terms.add(run)
        window = min(len(run) - 1, 2)
        for size in range(2, window + 2):
            for index in range(0, len(run) - size + 1):
                terms.add(run[index : index + size])
                if len(terms) >= 256:
                    return terms
    return terms


def _overlap_score(item: MemoryItem, query_terms: set[str]) -> int:
    return len(_text_terms(item.content) & query_terms)


def _recent_key(item: MemoryItem) -> datetime:
    return (
        item.last_used_at
        or item.updated_at
        or item.created_at
        or datetime.min.replace(tzinfo=UTC)
    )


def default_memory_repository() -> MemoryRepository:
    """Return the default in-memory repository for tests and local use."""

    return InMemoryMemoryRepository()
