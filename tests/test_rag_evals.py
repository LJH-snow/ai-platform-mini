from __future__ import annotations

import io
import json
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.evals import (
    RAGDatasetError,
    RAGEvalCase,
    RAGEvaluationRunner,
    RAGExecution,
    RetrievalOutcome,
    RetrievalReference,
    context_recall_at_k,
    rag_dataset_to_jsonl,
    read_rag_golden_dataset,
    reciprocal_rank_at_k,
    write_rag_golden_dataset,
)
from app.evals.retrievers import (
    EmbeddingVectorStoreRetriever,
    RAGServiceRetriever,
)
from app.exceptions.base import (
    KnowledgeBaseEmptyError,
    NoRelevantContextError,
    ProviderUnavailableError,
)
from app.rag.service import PreparedRAGRequest, RAGReference, RAGService
from app.rag.vector_store import SearchResult
from app.schemas.chat import ChatRequest

_FIXTURE = Path("tests/fixtures/evals/rag_golden.jsonl")
_OWNER = "a" * 64


class _StubRAGService:
    """AsyncMock-backed stand-in for the production RAG service."""

    def __init__(self) -> None:
        self.prepare_mock = AsyncMock()

    async def prepare(
        self,
        request: ChatRequest,
        *,
        owner_key_hash: str,
    ) -> PreparedRAGRequest:
        result = await self.prepare_mock(request, owner_key_hash=owner_key_hash)
        if isinstance(result, Exception):
            raise result
        return cast(PreparedRAGRequest, result)


def _ref(
    document_id: str,
    chunk_id: str,
    chunk_index: int,
    *,
    distance: float = 0.1,
) -> RetrievalReference:
    return RetrievalReference(
        document_id=document_id,
        chunk_id=chunk_id,
        chunk_index=chunk_index,
        distance=distance,
    )


_CASE_REFERENCES: dict[str, tuple[RetrievalReference, ...]] = {
    "rag-answer-check": (_ref("doc-policy", "chunk-refund-01", 0),),
    "rag-doc-level": (_ref("doc-handbook", "chunk-handbook-01", 0),),
    "rag-full-hit": (
        _ref("doc-policy", "chunk-refund-01", 0),
        _ref("doc-policy", "chunk-refund-02", 1),
    ),
    "rag-k-truncated": (
        _ref("doc-policy", "chunk-shipping-01", 0),
        _ref("doc-policy", "chunk-shipping-02", 1),
    ),
    "rag-no-hit": (_ref("doc-other", "chunk-other", 0),),
    "rag-partial-hit": (
        _ref("doc-policy", "chunk-shipping-01", 0),
        _ref("doc-policy", "chunk-shipping-03", 1),
    ),
}


async def _fake_run_case(
    case: RAGEvalCase,
) -> RetrievalOutcome | RAGExecution:
    if case.case_id == "rag-failed":
        raise RuntimeError("fixture failure")
    if case.case_id == "rag-no-sources":
        return RetrievalOutcome(
            (),
            status="no_sources",
            error="knowledge_base_empty",
        )
    references = _CASE_REFERENCES.get(case.case_id, ())
    outcome = RetrievalOutcome(references=references, status="success")
    if case.case_id == "rag-answer-check":
        return RAGExecution(retrieval=outcome, answer="The needle is present.")
    return outcome


def test_context_recall_at_k_is_deterministic_and_validated() -> None:
    assert context_recall_at_k(("c1", "c2"), ("c1", "c3")) == pytest.approx(0.5)
    assert context_recall_at_k(("c1",), ("c1", "c2"), k=1) == 1.0
    assert context_recall_at_k(("c2",), ("c1", "c2"), k=1) == 0.0
    assert context_recall_at_k(("c1",), ()) == 0.0
    assert context_recall_at_k(("c1", "c1"), ("c1",)) == 1.0

    with pytest.raises(ValueError, match="expected_ids"):
        context_recall_at_k((), ())
    with pytest.raises(ValueError, match="positive integer"):
        context_recall_at_k(("c1",), ("c1",), k=0)


def test_reciprocal_rank_at_k_is_deterministic_and_validated() -> None:
    assert reciprocal_rank_at_k(("d2",), ("d1", "d2")) == pytest.approx(0.5)
    assert reciprocal_rank_at_k(("d1",), ("d3", "d4")) == 0.0
    assert reciprocal_rank_at_k(("d1",), ("d1", "d2")) == 1.0
    assert reciprocal_rank_at_k(("d2",), ("d1", "d2"), k=1) == 0.0
    assert reciprocal_rank_at_k(("d2",), ("d1", "d1", "d2")) == pytest.approx(0.5)
    assert reciprocal_rank_at_k(("d1",), ()) == 0.0

    with pytest.raises(ValueError, match="expected_ids"):
        reciprocal_rank_at_k((), ())
    with pytest.raises(ValueError, match="positive integer"):
        reciprocal_rank_at_k(("d1",), ("d1",), k=0)


def test_rag_case_jsonl_round_trip_and_fixture_load() -> None:
    cases = (
        RAGEvalCase(
            case_id="one",
            query="question",
            expected_document_ids=("doc-1",),
            expected_chunk_ids=("chunk-1",),
            expected_answer_contains="answer",
            top_k=5,
            metadata={"label": "离线"},
        ),
        RAGEvalCase(case_id="two", query="q2", expected_document_ids="doc-2"),
    )

    first = rag_dataset_to_jsonl(cases)
    restored = read_rag_golden_dataset(first)

    assert restored == cases
    assert rag_dataset_to_jsonl(restored) == first

    stream = io.StringIO()
    write_rag_golden_dataset(cases, stream)
    assert read_rag_golden_dataset(stream.getvalue()) == cases
    assert len(read_rag_golden_dataset(_FIXTURE)) == 8


def test_rag_case_constructor_rejects_invalid_contracts() -> None:
    with pytest.raises(ValueError, match="at least one"):
        RAGEvalCase(case_id="bad", query="q")
    with pytest.raises(ValueError, match="duplicates"):
        RAGEvalCase(case_id="bad", query="q", expected_chunk_ids=("c", "c"))
    with pytest.raises(ValueError, match="top_k"):
        RAGEvalCase(case_id="bad", query="q", expected_chunk_ids=("c",), top_k=0)
    with pytest.raises(ValueError, match="unknown RAG case fields"):
        RAGEvalCase.from_dict(
            {
                "case_id": "x",
                "query": "q",
                "expected_chunk_ids": ["c"],
                "unexpected": True,
            }
        )
    with pytest.raises(RAGDatasetError, match="at least one"):
        read_rag_golden_dataset('{"case_id":"x","query":"q"}')


@pytest.mark.asyncio
async def test_rag_runner_computes_case_and_aggregate_metrics() -> None:
    report = await RAGEvaluationRunner(_fake_run_case).run(
        read_rag_golden_dataset(_FIXTURE)
    )
    by_id = {result.case_id: result for result in report.results}

    assert report.summary.case_count == 8
    assert report.summary.retrieval_success_count == 6
    assert report.summary.retrieval_success_rate == pytest.approx(0.75)
    assert report.summary.context_recall_at_k == pytest.approx(0.5)
    assert report.summary.document_recall_at_k == pytest.approx(5 / 7)
    assert report.summary.chunk_recall_at_k == pytest.approx(2.5 / 6)
    assert report.summary.context_mrr_at_k == pytest.approx(4 / 7)
    assert report.summary.document_mrr_at_k == pytest.approx(5 / 7)
    assert report.summary.content_mrr_at_k is None
    assert report.summary.answer_correctness_accuracy == 1.0
    assert report.summary.answer_correctness_case_count == 1
    assert report.summary.average_retrieved_chunks == 1.0

    assert by_id["rag-full-hit"].success is True
    assert by_id["rag-full-hit"].context_recall_at_k == 1.0
    assert by_id["rag-full-hit"].document_recall_at_k == 1.0
    assert by_id["rag-partial-hit"].context_recall_at_k == pytest.approx(0.5)
    assert by_id["rag-partial-hit"].chunk_recall_at_k == pytest.approx(0.5)
    assert by_id["rag-partial-hit"].chunk_mrr_at_k == 1.0
    assert by_id["rag-doc-level"].context_recall_at_k == 1.0
    assert by_id["rag-no-hit"].context_recall_at_k == 0.0
    assert by_id["rag-k-truncated"].context_recall_at_k == 0.0
    assert by_id["rag-k-truncated"].context_mrr_at_k == 0.0
    assert by_id["rag-k-truncated"].document_mrr_at_k == 1.0
    assert by_id["rag-k-truncated"].retrieved_count == 1
    assert by_id["rag-answer-check"].answer_correct is True
    assert by_id["rag-answer-check"].context_recall_at_k == 1.0
    assert by_id["rag-full-hit"].context_mrr_at_k == 1.0

    assert by_id["rag-no-sources"].status == "no_sources"
    assert by_id["rag-no-sources"].success is False
    assert by_id["rag-no-sources"].context_recall_at_k == 0.0
    assert by_id["rag-no-sources"].context_mrr_at_k == 0.0
    assert by_id["rag-no-hit"].document_mrr_at_k == 0.0
    assert by_id["rag-failed"].status == "failed"
    assert by_id["rag-failed"].error == "RuntimeError"
    assert by_id["rag-failed"].context_recall_at_k is None
    assert by_id["rag-failed"].context_mrr_at_k is None
    assert by_id["rag-failed"].document_mrr_at_k is None


@pytest.mark.asyncio
async def test_rag_runner_fail_fast_and_report_serialization_are_safe() -> None:
    cases = (
        RAGEvalCase(case_id="boom", query="q", expected_chunk_ids=("c",)),
        RAGEvalCase(case_id="after", query="q", expected_chunk_ids=("c",)),
    )

    async def boom(case: RAGEvalCase) -> RetrievalOutcome:
        if case.case_id == "boom":
            raise RuntimeError("stop")
        return RetrievalOutcome((_ref("d", "c", 0),), status="success")

    report = await RAGEvaluationRunner(boom, fail_fast=True).run(cases)
    assert [result.case_id for result in report.results] == ["boom"]

    full = await RAGEvaluationRunner(_fake_run_case).run(
        read_rag_golden_dataset(_FIXTURE)
    )
    serialized = full.to_json()
    payload = json.loads(serialized)
    assert payload["summary"]["case_count"] == 8
    assert "context_mrr_at_k" in payload["summary"]
    assert '"content":' not in serialized
    assert "owner_key_hash" not in serialized
    assert payload["results"][0]["expected_chunk_ids"]


@pytest.mark.asyncio
async def test_rag_runner_failed_outcome_has_null_metrics_and_uses_latency() -> None:
    case = RAGEvalCase(
        case_id="failed-outcome",
        query="q",
        expected_chunk_ids=("c1",),
        expected_answer_contains="needle",
    )

    async def failed_outcome(_: RAGEvalCase) -> RetrievalOutcome:
        return RetrievalOutcome(
            (),
            status="failed",
            error="ProviderUnavailableError",
            latency_ms=7.5,
        )

    result = (await RAGEvaluationRunner(failed_outcome).run((case,))).results[0]

    assert result.status == "failed"
    assert result.success is False
    assert result.context_recall_at_k is None
    assert result.document_recall_at_k is None
    assert result.chunk_recall_at_k is None
    assert result.answer_correct is False
    assert result.retrieved_count == 0
    assert result.latency_ms == 7.5
    assert result.error == "ProviderUnavailableError"


@pytest.mark.asyncio
async def test_rag_runner_prefers_outcome_reported_latency() -> None:
    case = RAGEvalCase(case_id="latency", query="q", expected_chunk_ids=("c1",))

    async def run_case(_: RAGEvalCase) -> RetrievalOutcome:
        return RetrievalOutcome(
            (_ref("d1", "c1", 0),),
            status="success",
            latency_ms=3.25,
        )

    result = (await RAGEvaluationRunner(run_case).run((case,))).results[0]

    assert result.latency_ms == 3.25


def test_retrieval_outcome_rejects_boolean_latency() -> None:
    with pytest.raises(ValueError, match="latency_ms"):
        RetrievalOutcome(latency_ms=True)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad_latency",
    [float("nan"), float("inf"), -1.0],
)
def test_retrieval_outcome_rejects_invalid_latency_values(bad_latency: float) -> None:
    with pytest.raises(ValueError, match="latency_ms"):
        RetrievalOutcome(latency_ms=bad_latency)


def test_embedding_retriever_rejects_non_finite_max_distance() -> None:
    embedder = AsyncMock()
    store = AsyncMock()

    for bad_distance in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="max_distance"):
            EmbeddingVectorStoreRetriever(
                embedder,
                store,
                top_k=5,
                max_distance=bad_distance,
                owner_key_hash=_OWNER,
            )


@pytest.mark.asyncio
async def test_rag_runner_empty_dataset_has_explicit_summary() -> None:
    async def never_called(_: RAGEvalCase) -> RetrievalOutcome:
        raise AssertionError("empty dataset must not call run_case")

    report = await RAGEvaluationRunner(never_called).run(())

    assert report.results == ()
    assert report.summary.case_count == 0
    assert report.summary.retrieval_success_rate == 0.0
    assert report.summary.context_recall_at_k is None
    assert report.summary.average_retrieved_chunks == 0.0
    assert report.summary.p95_latency_ms == 0.0


@pytest.mark.asyncio
async def test_rag_service_retriever_projects_prepare_results() -> None:
    stub = _StubRAGService()
    stub.prepare_mock.return_value = PreparedRAGRequest(
        enhanced_request=ChatRequest(message="ignored"),
        chunk_ids=("c1",),
        references=(
            RAGReference(
                document_id="d1",
                chunk_id="c1",
                chunk_index=2,
                content="ignored",
                distance=0.12,
            ),
        ),
    )
    retriever = RAGServiceRetriever(
        cast(RAGService, stub),
        owner_key_hash=_OWNER,
    )

    outcome = await retriever.retrieve("what")

    assert outcome.status == "success"
    assert outcome.references == (
        RetrievalReference("d1", "c1", 2, distance=0.12, content="ignored"),
    )
    stub.prepare_mock.assert_awaited_once_with(
        ChatRequest(message="what"),
        owner_key_hash=_OWNER,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception", "status", "error_code"),
    [
        (
            KnowledgeBaseEmptyError("internal"),
            "no_sources",
            "knowledge_base_empty",
        ),
        (
            NoRelevantContextError("internal"),
            "no_sources",
            "no_relevant_context",
        ),
        (
            ProviderUnavailableError("internal"),
            "failed",
            "ProviderUnavailableError",
        ),
    ],
)
async def test_rag_service_retriever_maps_domain_errors_to_safe_outcomes(
    exception: Exception,
    status: str,
    error_code: str,
) -> None:
    stub = _StubRAGService()
    stub.prepare_mock.side_effect = exception
    retriever = RAGServiceRetriever(
        cast(RAGService, stub),
        owner_key_hash=_OWNER,
    )

    outcome = await retriever.retrieve("what")

    assert outcome.status == status
    assert outcome.error == error_code
    assert outcome.references == ()


@pytest.mark.asyncio
async def test_embedding_vector_store_retriever_filters_and_maps_empty() -> None:
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(return_value=[0.1, 0.2])
    store = AsyncMock()
    store.search = AsyncMock(
        return_value=[
            SearchResult(
                document_id="d1",
                chunk_id="c1",
                chunk_index=0,
                content="ignored",
                distance=0.2,
            )
        ]
    )
    retriever = EmbeddingVectorStoreRetriever(
        embedder,
        store,
        top_k=5,
        max_distance=0.35,
        owner_key_hash=_OWNER,
    )

    outcome = await retriever.retrieve("what")

    assert outcome.status == "success"
    assert outcome.references == (
        RetrievalReference("d1", "c1", 0, distance=0.2, content="ignored"),
    )
    embedder.embed_query.assert_awaited_once_with("what")
    store.search.assert_awaited_once_with(
        [0.1, 0.2],
        5,
        owner_key_hash=_OWNER,
    )

    store.search.return_value = []
    empty = await retriever.retrieve("what")
    assert empty.status == "no_sources"
    assert empty.error == "knowledge_base_empty"

    store.search.return_value = [
        SearchResult(
            document_id="d1",
            chunk_id="c1",
            chunk_index=0,
            content="ignored",
            distance=0.5,
        )
    ]
    filtered = await retriever.retrieve("what")
    assert filtered.status == "no_sources"
    assert filtered.error == "no_relevant_context"


@pytest.mark.asyncio
async def test_rag_ci_regression_meets_thresholds() -> None:
    """Run the golden dataset through a fake retriever and assert CI thresholds.

    This test is deterministic, offline, and must never call a real LLM.
    Thresholds are chosen from the known fixture behavior; if they fail,
    the retriever contract or the golden data has drifted.
    """
    report = await RAGEvaluationRunner(_fake_run_case).run(
        read_rag_golden_dataset(_FIXTURE)
    )
    summary = report.summary

    assert summary.case_count == 8
    assert summary.retrieval_success_rate >= 0.5
    assert summary.retrieval_success_count >= 4
    assert summary.context_recall_at_k is not None
    assert summary.context_recall_at_k >= 0.4
    assert summary.document_recall_at_k is not None
    assert summary.document_recall_at_k >= 0.4
    assert summary.context_mrr_at_k is not None
    assert summary.context_mrr_at_k >= 0.4
    assert summary.document_mrr_at_k is not None
    assert summary.document_mrr_at_k >= 0.4
    assert summary.chunk_recall_at_k is not None
    assert summary.chunk_recall_at_k >= 0.2
    assert summary.answer_correctness_accuracy == 1.0
    assert summary.answer_correctness_case_count >= 1
    assert summary.average_retrieved_chunks >= 0.5
    assert summary.p95_latency_ms >= 0.0
