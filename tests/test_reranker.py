"""Reranker tests: protocol, Noop pass-through, Jina mapping, failure degrade."""

from __future__ import annotations

import httpx
import pytest

from app.rag.reranker import (
    JinaReranker,
    NoopReranker,
    Reranker,
    create_reranker,
)
from app.rag.vector_store import SearchResult


def _result(chunk_id: str, distance: float) -> SearchResult:
    return SearchResult(
        document_id="doc-1",
        chunk_id=chunk_id,
        chunk_index=0,
        content=f"content {chunk_id}",
        distance=distance,
    )


def _json_response(data: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


async def test_noop_preserves_order_and_objects() -> None:
    results = [_result("a", 0.1), _result("b", 0.2)]
    reranked = await NoopReranker().rerank("query", results)

    assert reranked == results
    assert reranked is not results  # defensive copy
    assert reranked[0].chunk_id == "a"


async def test_noop_is_returned_without_api_key() -> None:
    assert isinstance(create_reranker(""), NoopReranker)


async def test_jina_is_returned_with_api_key() -> None:
    reranker = create_reranker("sk-test")
    assert isinstance(reranker, JinaReranker)
    assert isinstance(reranker, Reranker)
    await reranker.close()


async def test_jina_reorders_and_maps_distance() -> None:
    transport = httpx.MockTransport(
        lambda request: _json_response(
            [
                {"index": 1, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.4},
                {"index": 2, "relevance_score": 0.7},
            ]
        )
    )
    reranker = JinaReranker("sk-test", transport=transport)
    results = [_result("a", 0.1), _result("b", 0.2), _result("c", 0.3)]

    reranked = await reranker.rerank("query", results)

    assert [r.chunk_id for r in reranked] == ["b", "c", "a"]
    assert reranked[0].distance == pytest.approx(0.1)  # 1 - 0.9
    assert reranked[1].distance == pytest.approx(0.3)  # 1 - 0.7
    assert reranked[2].distance == pytest.approx(0.6)  # 1 - 0.4
    assert all(0.0 <= r.distance <= 1.0 for r in reranked)
    await reranker.close()


async def test_jina_failure_degrades_to_original_order() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ConnectError("network down")

    transport = httpx.MockTransport(_boom)
    reranker = JinaReranker("sk-test", transport=transport)
    results = [_result("a", 0.1), _result("b", 0.2)]

    reranked = await reranker.rerank("query", results)

    assert [r.chunk_id for r in reranked] == ["a", "b"]
    assert reranked[0].distance == 0.1  # unchanged distances too
    await reranker.close()


async def test_jina_malformed_response_degrades_to_original_order() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": "not-a-list"})
    )
    reranker = JinaReranker("sk-test", transport=transport)
    results = [_result("a", 0.1), _result("b", 0.2)]

    reranked = await reranker.rerank("query", results)

    assert [r.chunk_id for r in reranked] == ["a", "b"]
    await reranker.close()


async def test_jina_score_out_of_range_is_clamped() -> None:
    transport = httpx.MockTransport(
        lambda request: _json_response(
            [
                {"index": 0, "relevance_score": 1.7},
                {"index": 1, "relevance_score": -0.3},
            ]
        )
    )
    reranker = JinaReranker("sk-test", transport=transport)
    results = [_result("a", 0.1), _result("b", 0.2)]

    reranked = await reranker.rerank("query", results)

    assert reranked[0].distance == 0.0  # clamped from 1.7
    assert reranked[1].distance == 1.0  # clamped from -0.3
    await reranker.close()


async def test_empty_results_short_circuit() -> None:
    transport = httpx.MockTransport(
        lambda request: _json_response([{"index": 0, "relevance_score": 1.0}])
    )
    reranker = JinaReranker("sk-test", transport=transport)

    assert await reranker.rerank("query", []) == []
    await reranker.close()
