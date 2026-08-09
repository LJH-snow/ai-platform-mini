"""Content-expectation golden cases and deterministic mock embedder tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from app.evals.mock_embedder import MockEmbedder
from app.evals.rag_jsonl import read_rag_golden_dataset
from app.evals.rag_models import (
    RAGEvalCase,
    RetrievalOutcome,
    RetrievalReference,
)
from app.evals.rag_runner import RAGEvaluationRunner


def _reference(chunk_id: str, content: str) -> RetrievalReference:
    return RetrievalReference(
        document_id="doc-1",
        chunk_id=chunk_id,
        chunk_index=0,
        distance=0.1,
        content=content,
    )


def _outcome(*references: RetrievalReference) -> RetrievalOutcome:
    return RetrievalOutcome(references=references, status="success")


def _retrieve(
    *references: RetrievalReference,
) -> Callable[[RAGEvalCase], Awaitable[RetrievalOutcome]]:
    """Return an async run-case callable yielding the given references."""

    async def _run(_case: RAGEvalCase) -> RetrievalOutcome:
        del _case
        return _outcome(*references)

    return _run


async def test_content_expectation_hits_when_reference_contains_fragment() -> None:
    case = RAGEvalCase(
        case_id="ci-error-code",
        query="E10023 错误码",
        expected_content_contains="E10023",
    )
    runner = RAGEvaluationRunner(_retrieve(_reference("c1", "E10023 错误码处理指南")))

    report = await runner.run([case])

    assert report.results[0].content_hit is True
    assert report.results[0].content_mrr_at_k == 1.0
    assert report.summary.content_hit_rate == 1.0
    assert report.summary.content_mrr_at_k == 1.0
    assert report.summary.content_expected_count == 1


async def test_content_expectation_mrr_rank_two_hit() -> None:
    case = RAGEvalCase(
        case_id="ci-rank2",
        query="E10023 错误码",
        expected_content_contains="E10023",
    )
    runner = RAGEvaluationRunner(
        _retrieve(
            _reference("c1", "完全无关的文本"),
            _reference("c2", "E10023 错误码处理指南"),
        )
    )

    report = await runner.run([case])

    assert report.results[0].content_hit is True
    assert report.results[0].content_mrr_at_k == pytest.approx(0.5)
    assert report.summary.content_mrr_at_k == pytest.approx(0.5)


async def test_content_expectation_misses_when_fragment_absent() -> None:
    case = RAGEvalCase(
        case_id="ci-miss",
        query="其他内容",
        expected_content_contains="E10023",
    )
    runner = RAGEvaluationRunner(_retrieve(_reference("c1", "完全无关的文本")))

    report = await runner.run([case])

    assert report.results[0].content_hit is False
    assert report.results[0].content_mrr_at_k == 0.0
    assert report.summary.content_hit_rate == 0.0
    assert report.summary.content_mrr_at_k == 0.0


async def test_content_expectation_ignored_when_not_configured() -> None:
    case = RAGEvalCase(case_id="id-based", query="q", expected_chunk_ids=("c1",))
    runner = RAGEvaluationRunner(_retrieve(_reference("c1", "任何内容")))

    report = await runner.run([case])

    assert report.results[0].content_hit is None
    assert report.results[0].content_mrr_at_k is None
    assert report.summary.content_hit_rate is None
    assert report.summary.content_mrr_at_k is None
    assert report.summary.content_expected_count == 0


def test_content_expectation_parses_from_jsonl_fixture() -> None:
    from pathlib import Path

    cases = read_rag_golden_dataset(Path("tests/fixtures/evals/rag_golden_ci.jsonl"))
    assert len(cases) == 4
    assert all(case.expected_content_contains for case in cases)
    assert cases[0].expected_content_contains == "E10023"


def test_content_expectation_requires_at_least_one_expectation() -> None:
    with pytest.raises(ValueError):
        RAGEvalCase(case_id="empty", query="q")


# ── MockEmbedder ─────────────────────────────────────────────────────────────


async def test_mock_embedder_is_deterministic() -> None:
    embedder = MockEmbedder(dimensions=32)
    first = await embedder.embed(["报销政策文本"])
    second = await embedder.embed(["报销政策文本"])
    assert first == second


async def test_mock_embedder_similarity_gradient() -> None:
    embedder = MockEmbedder(dimensions=64)
    await embedder.embed(
        [
            "报销政策：差旅报销需要发票",
            "退款规则：三十天无理由退款",
        ]
    )
    query = await embedder.embed_query("报销发票")
    expense = (await embedder.embed(["报销政策：差旅报销需要发票"]))[0]
    refund = (await embedder.embed(["退款规则：三十天无理由退款"]))[0]

    def _cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    # Shared tokens (报销/发票) make the expense vector closer to the query
    # than the refund vector; gradient is measurable and deterministic.
    assert _cosine(query, expense) > _cosine(query, refund)


async def test_mock_embedder_rare_token_dominates() -> None:
    embedder = MockEmbedder(dimensions=64)
    await embedder.embed(
        [
            "E10023 错误码处理指南",
            "公司办公地址与年报",
            "公司简介与年报摘要",
        ]
    )
    query = await embedder.embed_query("E10023")
    error_chunk = (await embedder.embed(["E10023 错误码处理指南"]))[0]
    company_chunk = (await embedder.embed(["公司办公地址与年报"]))[0]

    def _cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    # E10023 is rare in the corpus, so it dominates the query vector and
    # the chunk containing it is closest.
    assert _cosine(query, error_chunk) > _cosine(query, company_chunk)


async def test_mock_embedder_close_is_safe() -> None:
    await MockEmbedder().close()
