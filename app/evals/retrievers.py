"""Production adapters that execute RAG retrieval during evaluation."""

from __future__ import annotations

import math

from app.evals.rag_models import RetrievalOutcome, RetrievalReference
from app.exceptions.base import (
    KnowledgeBaseEmptyError,
    NoRelevantContextError,
    ProviderError,
    ProviderUnavailableError,
    RAGStorageUnavailableError,
    RAGUnavailableError,
)
from app.rag.embedder import Embedder
from app.rag.service import RAGReference, RAGService
from app.rag.vector_store import VectorStore, validate_owner_key_hash
from app.schemas.chat import ChatRequest

_RETRIEVAL_FAILURES = (
    RAGUnavailableError,
    RAGStorageUnavailableError,
    ProviderUnavailableError,
    ProviderError,
)


class RAGServiceRetriever:
    """Retrieve through the production ``RAGService.prepare`` path."""

    def __init__(self, rag_service: RAGService, owner_key_hash: str) -> None:
        self._rag_service = rag_service
        self._owner_key_hash = validate_owner_key_hash(owner_key_hash)

    async def retrieve(self, query: str) -> RetrievalOutcome:
        """Prepare a RAG request and project only stable retrieval metadata."""

        if not query.strip():
            return RetrievalOutcome((), status="no_sources", error="invalid_query")
        try:
            prepared = await self._rag_service.prepare(
                ChatRequest(message=query),
                owner_key_hash=self._owner_key_hash,
            )
        except KnowledgeBaseEmptyError:
            return RetrievalOutcome(
                (),
                status="no_sources",
                error="knowledge_base_empty",
            )
        except NoRelevantContextError:
            return RetrievalOutcome(
                (),
                status="no_sources",
                error="no_relevant_context",
            )
        except _RETRIEVAL_FAILURES as exc:
            return RetrievalOutcome((), status="failed", error=type(exc).__name__)
        return RetrievalOutcome(
            references=tuple(
                _to_retrieval_reference(reference) for reference in prepared.references
            ),
            status="success",
        )


class EmbeddingVectorStoreRetriever:
    """Retrieve directly through the production embedder and vector store."""

    def __init__(
        self,
        embedder: Embedder,
        vector_store: VectorStore,
        *,
        top_k: int,
        max_distance: float,
        owner_key_hash: str,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        self._top_k = top_k
        if (
            isinstance(max_distance, bool)
            or not isinstance(max_distance, (int, float))
            or not math.isfinite(max_distance)
            or max_distance < 0
        ):
            raise ValueError("max_distance must be a non-negative number")
        self._max_distance = float(max_distance)
        self._owner_key_hash = validate_owner_key_hash(owner_key_hash)

    async def retrieve(self, query: str) -> RetrievalOutcome:
        """Embed the query and search the vector store with the configured cap."""

        if not query.strip():
            return RetrievalOutcome((), status="no_sources", error="invalid_query")
        try:
            query_embedding = await self._embedder.embed_query(query)
            results = await self._vector_store.search(
                query_embedding,
                self._top_k,
                owner_key_hash=self._owner_key_hash,
            )
        except _RETRIEVAL_FAILURES as exc:
            return RetrievalOutcome((), status="failed", error=type(exc).__name__)
        if not results:
            return RetrievalOutcome(
                (),
                status="no_sources",
                error="knowledge_base_empty",
            )
        filtered = [
            result for result in results if result.distance <= self._max_distance
        ]
        if not filtered:
            return RetrievalOutcome(
                (),
                status="no_sources",
                error="no_relevant_context",
            )
        return RetrievalOutcome(
            references=tuple(
                RetrievalReference(
                    document_id=result.document_id,
                    chunk_id=result.chunk_id,
                    chunk_index=result.chunk_index,
                    distance=result.distance,
                    content=result.content,
                )
                for result in filtered
            ),
            status="success",
        )


def _to_retrieval_reference(reference: RAGReference) -> RetrievalReference:
    """Project a production reference into the eval-only metadata shape."""

    return RetrievalReference(
        document_id=reference.document_id,
        chunk_id=reference.chunk_id,
        chunk_index=reference.chunk_index,
        distance=reference.distance,
        content=reference.content,
    )
