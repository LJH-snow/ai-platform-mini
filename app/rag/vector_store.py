"""Tenant-aware vector-store contracts and safe RAG result types."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

OWNER_KEY_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_DOCUMENT_PREVIEW_CHARACTERS = 4_000


def validate_owner_key_hash(owner_key_hash: str | None) -> str:
    """Validate the already-hashed API-key tenant identifier.

    The vector store accepts only a lowercase SHA-256 hex digest. It never
    accepts or derives a tenant identifier from a raw API key.
    """

    if owner_key_hash is None or not OWNER_KEY_HASH_PATTERN.fullmatch(owner_key_hash):
        raise ValueError("owner_key_hash must be a lowercase SHA-256 hex digest")
    return owner_key_hash


def validate_document_id(document_id: str) -> str:
    """Validate and normalize the UUID used at the document boundary."""

    try:
        return str(UUID(document_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("document_id must be a valid UUID") from exc


@dataclass(frozen=True)
class DocumentSummary:
    """Safe metadata for one indexed knowledge-base document."""

    document_id: str
    filename: str
    content_sha256: str
    embedding_model: str
    embedding_dimensions: int
    created_at: datetime | None
    chunk_count: int
    text_characters: int
    # Prompt-injection safety verdict (clean|suspicious|malicious);
    # None pre-dates safety tracking.
    safety_verdict: str | None = None


@dataclass(frozen=True)
class DocumentPreview:
    """Bounded document text intended for authenticated preview flows."""

    document_id: str
    filename: str
    content: str
    truncated: bool


@dataclass(frozen=True)
class SearchResult:
    document_id: str
    chunk_id: str
    chunk_index: int
    content: str
    distance: float


@dataclass(frozen=True)
class KeywordSearchResult:
    """Raw keyword-rank output of the concrete keyword search path.

    Deliberately not part of the ``VectorStore`` protocol: keyword
    ranking is a concrete ``PgVectorStore`` capability that
    ``HybridRetriever`` composes, maps to ``SearchResult.distance``, and
    exposes through the protocol.
    """

    document_id: str
    chunk_id: str
    chunk_index: int
    content: str
    rank: float


@runtime_checkable
class VectorStore(Protocol):
    async def add_document(
        self,
        source_path: str,
        content_sha256: str,
        embedding_model: str,
        embedding_dimensions: int,
        chunks: list[str],
        embeddings: list[list[float]],
        *,
        owner_key_hash: str | None = None,
        safety_verdict: str | None = None,
        safety_detail: dict[str, object] | None = None,
    ) -> str: ...

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        *,
        owner_key_hash: str | None = None,
        query: str | None = None,
    ) -> list[SearchResult]: ...

    async def get_document_summary(
        self, document_id: str, *, owner_key_hash: str | None = None
    ) -> DocumentSummary | None: ...

    async def list_documents(
        self, *, owner_key_hash: str | None = None
    ) -> list[DocumentSummary]: ...

    async def delete_document(self, owner_key_hash: str, document_id: str) -> bool: ...

    async def get_document_preview(
        self,
        owner_key_hash: str,
        document_id: str,
        *,
        max_characters: int = MAX_DOCUMENT_PREVIEW_CHARACTERS,
    ) -> DocumentPreview | None: ...
