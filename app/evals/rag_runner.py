"""Deterministic, dependency-injected RAG evaluation runner."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterable

from app.evals.matching import answer_matches_expected
from app.evals.rag_jsonl import validate_rag_dataset
from app.evals.rag_models import (
    RAGEvalCase,
    RAGEvalCaseResult,
    RAGExecution,
    RAGReport,
    RAGSummary,
    RetrievalOutcome,
    RetrievalReference,
    context_recall_at_k,
    reciprocal_rank_at_k,
)
from app.evals.stats import average, percentile

type RunRAGCase = Callable[[RAGEvalCase], Awaitable[RetrievalOutcome | RAGExecution]]


class RAGEvaluationRunner:
    """Run golden RAG cases sequentially through an injected callable."""

    def __init__(self, run_case: RunRAGCase, *, fail_fast: bool = False) -> None:
        self._run_case = run_case
        self._fail_fast = fail_fast

    async def run(self, cases: Iterable[RAGEvalCase]) -> RAGReport:
        """Evaluate cases in dataset order with per-case exception isolation."""

        dataset = validate_rag_dataset(tuple(cases))
        results: list[RAGEvalCaseResult] = []
        for case in dataset:
            started = time.perf_counter()
            try:
                outcome, answer = _normalize_execution(await self._run_case(case))
                result = _evaluate_rag_case(
                    case,
                    outcome,
                    answer,
                    _elapsed_ms(started),
                )
            except Exception as exc:
                result = _failed_rag_case_result(
                    case,
                    _elapsed_ms(started),
                    type(exc).__name__,
                )
            results.append(result)
            if self._fail_fast and not result.success:
                break
        return _build_rag_report(tuple(results))

    async def evaluate(self, cases: Iterable[RAGEvalCase]) -> RAGReport:
        """Descriptive alias for :meth:`run`."""

        return await self.run(cases)


def _normalize_execution(
    execution: RetrievalOutcome | RAGExecution,
) -> tuple[RetrievalOutcome, str | None]:
    """Adapt a retrieval-only or answer-paired observation."""

    if isinstance(execution, RAGExecution):
        return execution.retrieval, execution.answer
    if isinstance(execution, RetrievalOutcome):
        return execution, None
    raise TypeError("run_case must return RetrievalOutcome or RAGExecution")


def _evaluate_rag_case(
    case: RAGEvalCase,
    outcome: RetrievalOutcome,
    answer: str | None,
    latency_ms: float,
) -> RAGEvalCaseResult:
    """Apply deterministic RAG metrics to one normalized observation."""

    latency_ms = outcome.latency_ms if outcome.latency_ms is not None else latency_ms
    if outcome.status == "failed":
        return _failed_rag_case_result(case, latency_ms, outcome.error)

    selected = _select_references(outcome.references, case.top_k)
    retrieved_documents = _unique_document_ids(selected)
    retrieved_chunks = tuple(reference.chunk_id for reference in selected)

    document_recall = (
        context_recall_at_k(case.document_ids, retrieved_documents)
        if case.document_ids
        else None
    )
    chunk_recall = (
        context_recall_at_k(case.chunk_ids, retrieved_chunks)
        if case.chunk_ids
        else None
    )
    context_recall = chunk_recall if case.chunk_ids else document_recall
    document_mrr = (
        reciprocal_rank_at_k(case.document_ids, retrieved_documents)
        if case.document_ids
        else None
    )
    chunk_mrr = (
        reciprocal_rank_at_k(case.chunk_ids, retrieved_chunks)
        if case.chunk_ids
        else None
    )
    context_mrr = chunk_mrr if case.chunk_ids else document_mrr
    content_mrr: float | None = None
    if case.expected_content_contains:
        content_mrr = 0.0
        for rank, reference in enumerate(selected, start=1):
            if case.expected_content_contains in (reference.content or ""):
                content_mrr = 1.0 / rank
                break
    answer_correct = (
        None
        if case.answer_fragments is None
        else answer_matches_expected(case.answer_fragments, answer)
    )
    content_hit: bool | None = None
    if case.expected_content_contains:
        content_hit = any(
            case.expected_content_contains in (reference.content or "")
            for reference in selected
        )
    return RAGEvalCaseResult(
        case_id=case.case_id,
        status=outcome.status,
        success=outcome.status == "success" and bool(outcome.references),
        expected_document_ids=case.document_ids,
        expected_chunk_ids=case.chunk_ids,
        retrieved_document_ids=retrieved_documents,
        retrieved_chunk_ids=retrieved_chunks,
        retrieved_count=len(retrieved_chunks),
        document_recall_at_k=document_recall,
        chunk_recall_at_k=chunk_recall,
        context_recall_at_k=context_recall,
        document_mrr_at_k=document_mrr,
        chunk_mrr_at_k=chunk_mrr,
        context_mrr_at_k=context_mrr,
        content_mrr_at_k=content_mrr,
        answer_correct=answer_correct,
        content_hit=content_hit,
        top_k=case.top_k,
        latency_ms=latency_ms,
        error=outcome.error,
    )


def _failed_rag_case_result(
    case: RAGEvalCase,
    latency_ms: float,
    error: str | None,
) -> RAGEvalCaseResult:
    """Convert an isolated callable exception into a safe failed result."""

    return RAGEvalCaseResult(
        case_id=case.case_id,
        status="failed",
        success=False,
        expected_document_ids=case.document_ids,
        expected_chunk_ids=case.chunk_ids,
        expected_content_contains=case.expected_content_contains,
        retrieved_document_ids=(),
        retrieved_chunk_ids=(),
        retrieved_count=0,
        document_recall_at_k=None,
        chunk_recall_at_k=None,
        context_recall_at_k=None,
        document_mrr_at_k=None,
        chunk_mrr_at_k=None,
        context_mrr_at_k=None,
        content_mrr_at_k=None,
        answer_correct=(False if case.answer_fragments is not None else None),
        top_k=case.top_k,
        latency_ms=latency_ms,
        error=error,
    )


def _select_references(
    references: tuple[RetrievalReference, ...],
    top_k: int | None,
) -> tuple[RetrievalReference, ...]:
    """Truncate retrieved references to the case-level retrieval depth."""

    if top_k is None:
        return references
    return references[:top_k]


def _unique_document_ids(
    references: tuple[RetrievalReference, ...],
) -> tuple[str, ...]:
    """Return retrieved document IDs in first-seen order without duplicates."""

    return tuple(dict.fromkeys(reference.document_id for reference in references))


def _build_rag_report(results: tuple[RAGEvalCaseResult, ...]) -> RAGReport:
    """Compute aggregate RAG metrics, including explicit empty-batch behavior."""

    count = len(results)
    successful = sum(result.success for result in results)
    summary = RAGSummary(
        case_count=count,
        retrieval_success_count=successful,
        retrieval_success_rate=(successful / count if count else 0.0),
        context_recall_at_k=_average_metric(
            [result.context_recall_at_k for result in results]
        ),
        document_recall_at_k=_average_metric(
            [result.document_recall_at_k for result in results]
        ),
        chunk_recall_at_k=_average_metric(
            [result.chunk_recall_at_k for result in results]
        ),
        answer_correctness_accuracy=_average_bool(
            [
                result.answer_correct
                for result in results
                if result.answer_correct is not None
            ]
        ),
        answer_correctness_case_count=sum(
            result.answer_correct is not None for result in results
        ),
        average_retrieved_chunks=(
            sum(result.retrieved_count for result in results) / count if count else 0.0
        ),
        p95_latency_ms=percentile(
            [result.latency_ms for result in results],
            percentile=0.95,
        ),
        document_mrr_at_k=_average_metric(
            [result.document_mrr_at_k for result in results]
        ),
        context_mrr_at_k=_average_metric(
            [result.context_mrr_at_k for result in results]
        ),
        content_mrr_at_k=_average_metric(
            [result.content_mrr_at_k for result in results]
        ),
        content_hit_rate=_average_bool(
            [result.content_hit for result in results if result.content_hit is not None]
        ),
        content_expected_count=sum(
            result.content_hit is not None for result in results
        ),
    )
    return RAGReport(results=results, summary=summary)


def _average_metric(values: list[float | None]) -> float | None:
    """Average defined metric values, returning None when none are defined."""

    defined = [value for value in values if value is not None]
    return average(defined) if defined else None


def _average_bool(values: list[bool]) -> float | None:
    """Average boolean observations as a float accuracy value."""

    return average([1.0 if value else 0.0 for value in values]) if values else None


def _elapsed_ms(started: float) -> float:
    """Return monotonic elapsed time in milliseconds."""

    return (time.perf_counter() - started) * 1000
