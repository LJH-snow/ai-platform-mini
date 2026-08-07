"""Hybrid vector + keyword retriever with RRF fusion.

``HybridRetriever`` implements the ``VectorStore`` protocol by delegating
persistence and summaries to a concrete ``PgVectorStore`` and composing
its two search paths:

* semantic path — ``PgVectorStore.search`` (cosine distance)
* keyword path — ``PgVectorStore.keyword_search`` (ts_rank over jieba
  token vectors)

Both paths fetch ``candidate_k`` results each; Reciprocal Rank Fusion
(``1 / (rrf_k + rank)``, weights fixed at 1.0 per the frozen design)
merges them and the top ``top_k`` are returned.

``SearchResult.distance`` semantics are preserved (lower = more
relevant): the fusion score is normalized against the best possible
score for the active mode and mapped to ``1 - score / max_score``.  In
practice this turns ``RAGService.max_distance`` into a relative
relevance threshold (e.g. 0.35 keeps results whose fused score is at
least 65% of the best possible score), which is the only meaningful
interpretation for a rank-based metric.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.rag.pg_vector_store import PgVectorStore
from app.rag.vector_store import (
    MAX_DOCUMENT_PREVIEW_CHARACTERS,
    DocumentPreview,
    DocumentSummary,
    KeywordSearchResult,
    SearchResult,
)

logger = logging.getLogger(__name__)

_DEFAULT_RRF_K = 60
_DEFAULT_CANDIDATE_K = 25


class HybridRetriever:
    """VectorStore implementation fusing semantic and keyword rankings."""

    def __init__(
        self,
        vector_store: PgVectorStore,
        *,
        rrf_k: int = _DEFAULT_RRF_K,
        candidate_k: int = _DEFAULT_CANDIDATE_K,
    ) -> None:
        self._vector_store = vector_store
        self._rrf_k = rrf_k
        self._candidate_k = candidate_k

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
    ) -> str:
        return await self._vector_store.add_document(
            source_path,
            content_sha256,
            embedding_model,
            embedding_dimensions,
            chunks,
            embeddings,
            owner_key_hash=owner_key_hash,
        )

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        *,
        owner_key_hash: str | None = None,
        query: str | None = None,
    ) -> list[SearchResult]:
        """Fuse semantic and keyword rankings via RRF.

        Without ``query`` text the keyword path is skipped and the
        semantic ranking is normalized on its own (keyword mode would
        not be reachable through this method anyway; it is used by callers
        that only have an embedding).
        """
        semantic = await self._vector_store.search(
            query_embedding,
            self._candidate_k,
            owner_key_hash=owner_key_hash,
        )
        keyword: list[KeywordSearchResult] = []
        if query:
            keyword = await self._vector_store.keyword_search(
                query,
                self._candidate_k,
                owner_key_hash=owner_key_hash,
            )
        return _fuse(semantic, keyword, top_k=top_k, rrf_k=self._rrf_k)

    # ── Delegated protocol members ───────────────────────────────────────

    async def get_document_summary(
        self, document_id: str, *, owner_key_hash: str | None = None
    ) -> DocumentSummary | None:
        return await self._vector_store.get_document_summary(
            document_id, owner_key_hash=owner_key_hash
        )

    async def list_documents(
        self, *, owner_key_hash: str | None = None
    ) -> list[DocumentSummary]:
        return await self._vector_store.list_documents(owner_key_hash=owner_key_hash)

    async def delete_document(self, owner_key_hash: str, document_id: str) -> bool:
        return await self._vector_store.delete_document(owner_key_hash, document_id)

    async def get_document_preview(
        self,
        owner_key_hash: str,
        document_id: str,
        *,
        max_characters: int = MAX_DOCUMENT_PREVIEW_CHARACTERS,
    ) -> DocumentPreview | None:
        return await self._vector_store.get_document_preview(
            owner_key_hash,
            document_id,
            max_characters=max_characters,
        )


def _fuse(
    semantic: list[SearchResult],
    keyword: list[KeywordSearchResult],
    *,
    top_k: int,
    rrf_k: int,
) -> list[SearchResult]:
    """Merge the two rankings with RRF and normalize into distance."""
    scores: dict[str, _FusedEntry] = {}
    for rank, semantic_result in enumerate(semantic, start=1):
        scores[semantic_result.chunk_id] = _FusedEntry(
            score=1.0 / (rrf_k + rank),
            chunk=_to_ranked_chunk(semantic_result),
        )
    for rank, keyword_result in enumerate(keyword, start=1):
        entry = scores.get(keyword_result.chunk_id)
        if entry is None:
            entry = _FusedEntry(score=0.0, chunk=_to_ranked_chunk(keyword_result))
            scores[keyword_result.chunk_id] = entry
        entry.score += 1.0 / (rrf_k + rank)

    if not scores:
        return []

    best_score = 2.0 / (rrf_k + 1) if semantic and keyword else 1.0 / (rrf_k + 1)
    fused = sorted(scores.values(), key=lambda entry: entry.score, reverse=True)[:top_k]
    return [
        SearchResult(
            document_id=entry.chunk.document_id,
            chunk_id=entry.chunk.chunk_id,
            chunk_index=entry.chunk.chunk_index,
            content=entry.chunk.content,
            distance=1.0 - entry.score / best_score,
        )
        for entry in fused
    ]


@dataclass
class _FusedEntry:
    score: float
    chunk: _RankedChunk


@dataclass(frozen=True)
class _RankedChunk:
    document_id: str
    chunk_id: str
    chunk_index: int
    content: str


def _to_ranked_chunk(result: SearchResult | KeywordSearchResult) -> _RankedChunk:
    return _RankedChunk(
        document_id=result.document_id,
        chunk_id=result.chunk_id,
        chunk_index=result.chunk_index,
        content=result.content,
    )
