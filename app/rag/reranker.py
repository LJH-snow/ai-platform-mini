"""Reranker protocol and implementations for post-fusion relevance ranking.

Reranking runs after retrieval fusion and may reorder results; the
``SearchResult`` contract (lower ``distance`` = more relevant) is
preserved by mapping relevance scores back onto ``1 - score``.

Design decisions (Sprint C2):

* availability first — any reranker failure degrades to pass-through
  with a warning instead of failing the whole RAG query;
* Jina is enabled only when ``RERANKER_API_KEY`` is set (loaded from the
  local ``.env`` only, never committed); without a key the
  ``NoopReranker`` is used;
* Cohere remains a documented extension point: implement the same
  ``Reranker`` protocol and swap it in ``create_reranker``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import httpx

from app.rag.vector_store import SearchResult

logger = logging.getLogger(__name__)

_JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"
_JINA_DEFAULT_MODEL = "jina-reranker-v2-base-multilingual"


@runtime_checkable
class Reranker(Protocol):
    """Async rerank boundary: reorder results for one query."""

    async def rerank(
        self, query: str, results: Sequence[SearchResult]
    ) -> list[SearchResult]: ...


class NoopReranker:
    """Pass-through reranker: preserves the fused order."""

    async def rerank(
        self, query: str, results: Sequence[SearchResult]
    ) -> list[SearchResult]:
        del query
        return list(results)


class JinaReranker:
    """HTTP reranker backed by the Jina Rerank API.

    On any network/provider failure the original order is returned with
    a warning so retrieval availability never depends on the reranker.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = _JINA_DEFAULT_MODEL,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=_JINA_RERANK_URL,
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def rerank(
        self, query: str, results: Sequence[SearchResult]
    ) -> list[SearchResult]:
        if not results:
            return []
        try:
            response = await self._client.post(
                "",
                json={
                    "model": self._model,
                    "query": query,
                    "documents": [result.content for result in results],
                    "top_n": len(results),
                },
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data")
            if not isinstance(data, list):
                raise ValueError("Jina rerank response missing data list")
            scores: dict[int, float] = {}
            for item in data:
                if not isinstance(item, dict):
                    continue
                index = item.get("index")
                score = item.get("relevance_score")
                if isinstance(index, int) and isinstance(score, int | float):
                    scores[index] = float(score)
            if not scores:
                raise ValueError("Jina rerank returned no usable scores")
            ordered = sorted(
                range(len(results)),
                key=lambda index: scores.get(index, 0.0),
                reverse=True,
            )
            return [
                _with_reranked_distance(results[index], scores.get(index))
                for index in ordered
            ]
        except Exception as exc:
            logger.warning("Jina rerank failed; returning original order: %s", exc)
            return list(results)


def _with_reranked_distance(result: SearchResult, score: float | None) -> SearchResult:
    """Map a relevance score back onto the lower-is-better distance contract."""
    if score is None:
        return result
    clamped = max(0.0, min(score, 1.0))
    return SearchResult(
        document_id=result.document_id,
        chunk_id=result.chunk_id,
        chunk_index=result.chunk_index,
        content=result.content,
        distance=1.0 - clamped,
    )


def create_reranker(
    api_key: str,
    *,
    model: str = _JINA_DEFAULT_MODEL,
    timeout_seconds: float = 10.0,
) -> Reranker:
    """Build the active reranker; without a key the Noop is used."""
    if not api_key:
        return NoopReranker()
    return JinaReranker(api_key, model=model, timeout_seconds=timeout_seconds)
