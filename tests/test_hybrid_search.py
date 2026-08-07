"""HybridRetriever tests: RRF fusion, distance semantics, protocol conformance."""

from __future__ import annotations

from typing import cast

from app.rag.hybrid import HybridRetriever
from app.rag.pg_vector_store import PgVectorStore
from app.rag.vector_store import (
    KeywordSearchResult,
    SearchResult,
    VectorStore,
)


class _FakeVectorStore:
    """Scripted PgVectorStore stand-in with controllable rankings."""

    def __init__(
        self,
        semantic: list[SearchResult] | None = None,
        keyword: list[KeywordSearchResult] | None = None,
    ) -> None:
        self._semantic = semantic or []
        self._keyword = keyword or []
        self.last_query: str | None = None

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        *,
        owner_key_hash: str | None = None,
        query: str | None = None,
    ) -> list[SearchResult]:
        del query_embedding, top_k, owner_key_hash, query
        return self._semantic

    async def keyword_search(
        self,
        query_text: str,
        top_k: int,
        *,
        owner_key_hash: str | None = None,
    ) -> list[KeywordSearchResult]:
        del top_k, owner_key_hash
        self.last_query = query_text
        return self._keyword


def _result(chunk_id: str, index: int) -> SearchResult:
    return SearchResult(
        document_id="doc-1",
        chunk_id=chunk_id,
        chunk_index=index,
        content=f"chunk {chunk_id}",
        distance=0.5,
    )


def _keyword(chunk_id: str, index: int, rank: float) -> KeywordSearchResult:
    return KeywordSearchResult(
        document_id="doc-1",
        chunk_id=chunk_id,
        chunk_index=index,
        content=f"chunk {chunk_id}",
        rank=rank,
    )


def _make(
    semantic: list[SearchResult], keyword: list[KeywordSearchResult]
) -> HybridRetriever:
    return HybridRetriever(
        cast(PgVectorStore, _FakeVectorStore(semantic, keyword)),
        rrf_k=60,
        candidate_k=25,
    )


async def test_hybrid_fuses_cross_path_results() -> None:
    """A chunk ranked low on one path but high on the other ranks overall."""
    semantic = [_result("b", 0), _result("c", 1), _result("a", 2)]
    keyword = [
        _keyword("b", 0, 1.0),  # b: keyword rank 1 (semantic rank 1 too)
        _keyword("a", 2, 0.9),  # a: keyword rank 2, semantic rank 3
    ]
    retriever = _make(semantic, keyword)

    results = await retriever.search([], top_k=3, query="报销")

    # b: 1/61 + 1/61 = 2/61 → best
    # a: 1/62(keyword#2) + 1/63(semantic#3) → second (cross-path gain)
    # c: 1/62(semantic#2) → third
    assert [r.chunk_id for r in results] == ["b", "a", "c"]
    assert retriever._vector_store.last_query == "报销"  # type: ignore[attr-defined]


async def test_distance_is_normalized_lower_is_better() -> None:
    semantic = [_result("b", 0), _result("a", 1)]
    keyword = [_keyword("b", 0, 1.0), _keyword("c", 2, 0.9)]
    retriever = _make(semantic, keyword)

    results = await retriever.search([], top_k=3, query="x")

    assert all(0.0 <= r.distance < 1.0 for r in results)
    assert results[0].chunk_id == "b"
    assert results[0].distance == 0.0  # best possible (both paths rank 1)
    distances = [r.distance for r in results]
    assert distances == sorted(distances)


async def test_vector_only_mode_without_query() -> None:
    """No query text → semantic-only path with single-path normalization."""
    semantic = [_result("a", 0), _result("b", 1)]
    retriever = _make(semantic, [])

    results = await retriever.search([], top_k=2, query=None)

    assert [r.chunk_id for r in results] == ["a", "b"]
    assert results[0].distance == 0.0  # semantic rank 1 → best of single path
    assert results[1].distance > results[0].distance


async def test_candidate_set_exceeds_final_top_k() -> None:
    """Two paths fetch candidate_k (25) each; fusion keeps only top_k."""
    semantic = [_result(f"s{i}", i) for i in range(25)]
    keyword = [_keyword(f"k{i}", i, 1.0) for i in range(25)]
    retriever = _make(semantic, keyword)

    results = await retriever.search([], top_k=5, query="x")

    assert len(results) == 5
    # A chunk present in both paths outranks single-path chunks even when
    # its individual ranks are worse (1/61 + 1/64 > 1/61).
    assert results[0].chunk_id in {"s0", "k0", "s1", "k1"}


async def test_empty_results_yield_empty_fusion() -> None:
    retriever = _make([], [])
    assert await retriever.search([], top_k=5, query="x") == []


async def test_hybrid_retriever_conforms_to_vector_store_protocol() -> None:
    retriever = _make([], [])
    assert isinstance(retriever, VectorStore)


def test_hybrid_accepts_pg_vector_store_type() -> None:
    """Constructor contract: wraps a concrete PgVectorStore."""
    retriever = _make([], [])
    assert isinstance(retriever, HybridRetriever)
    # The wrapped store is typed as PgVectorStore; duck-typed fakes pass.
    assert hasattr(retriever._vector_store, "keyword_search")  # type: ignore[attr-defined]
