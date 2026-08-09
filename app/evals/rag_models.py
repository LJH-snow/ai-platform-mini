"""Typed models and deterministic metrics for RAG golden evaluations."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.evals.models import (
    JSONValue,
    _is_json_value,
    _normalize_optional_strings,
    _parse_string_sequence,
)


def _normalize_id_sequence(
    value: str | Sequence[str] | None,
    *,
    field_name: str,
) -> tuple[str, ...]:
    """Normalize an optional string or string sequence into a tuple."""

    return _normalize_optional_strings(value, field_name=field_name) or ()


def _validate_unique_ids(ids: tuple[str, ...], *, field_name: str) -> None:
    """Reject duplicate IDs in one golden expectation list."""

    if len(set(ids)) != len(ids):
        raise ValueError(f"{field_name} must not contain duplicates")


def context_recall_at_k(
    expected_ids: Sequence[str],
    retrieved_ids: Sequence[str],
    *,
    k: int | None = None,
) -> float:
    """Return the fraction of expected IDs present in the top-k retrieved IDs.

    IDs are compared as a set so duplicates do not inflate recall. When ``k``
    is omitted, all retrieved IDs are considered.
    """

    if k is not None:
        if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
            raise ValueError("k must be a positive integer when provided")
    if any(not isinstance(item, str) or not item.strip() for item in expected_ids):
        raise ValueError("expected_ids must contain non-empty strings")
    if any(not isinstance(item, str) or not item.strip() for item in retrieved_ids):
        raise ValueError("retrieved_ids must contain non-empty strings")
    expected = set(expected_ids)
    if not expected:
        raise ValueError("expected_ids must not be empty")
    selected = retrieved_ids if k is None else retrieved_ids[:k]
    hits = len(expected & set(selected))
    return hits / len(expected)


def reciprocal_rank_at_k(
    expected_ids: Sequence[str],
    retrieved_ids: Sequence[str],
    *,
    k: int | None = None,
) -> float:
    """Return the reciprocal rank of the first expected hit within top-k.

    Follows TREC first-hit semantics: the score is 1 divided by the 1-based
    rank of the first retrieved ID that matches an expected ID, or 0.0 when
    no expected ID is found. Retrieved IDs are de-duplicated in first-seen
    order so repeated references cannot inflate the rank. When ``k`` is
    omitted, all retrieved IDs are considered.
    """

    if k is not None:
        if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
            raise ValueError("k must be a positive integer when provided")
    if any(not isinstance(item, str) or not item.strip() for item in expected_ids):
        raise ValueError("expected_ids must contain non-empty strings")
    if any(not isinstance(item, str) or not item.strip() for item in retrieved_ids):
        raise ValueError("retrieved_ids must contain non-empty strings")
    expected = set(expected_ids)
    if not expected:
        raise ValueError("expected_ids must not be empty")
    selected = retrieved_ids if k is None else retrieved_ids[:k]
    for rank, retrieved_id in enumerate(dict.fromkeys(selected), start=1):
        if retrieved_id in expected:
            return 1.0 / rank
    return 0.0


@dataclass(frozen=True)
class RAGEvalCase:
    """One serializable golden expectation for RAG retrieval quality.

    Expectations may target document/chunk IDs (stable only when the
    corpus is pre-seeded with known IDs) or content substrings
    (``expected_content_contains``) — the self-contained CI corpus uses
    content expectations because chunk UUIDs are not predictable.
    """

    case_id: str
    query: str
    expected_document_ids: str | Sequence[str] | None = None
    expected_chunk_ids: str | Sequence[str] | None = None
    expected_answer_contains: str | Sequence[str] | None = None
    expected_content_contains: str | None = None
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)
    top_k: int | None = None

    def __post_init__(self) -> None:
        """Validate and normalize the RAG golden-data contract."""

        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id must not be empty")
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must not be empty")
        if self.expected_content_contains is not None and not isinstance(
            self.expected_content_contains, str
        ):
            raise TypeError("expected_content_contains must be a string or null")
        if self.top_k is not None:
            if not isinstance(self.top_k, int) or isinstance(self.top_k, bool):
                raise TypeError("top_k must be an integer or null")
            if self.top_k <= 0:
                raise ValueError("top_k must be greater than zero")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")

        metadata = dict(self.metadata)
        if not all(isinstance(key, str) for key in metadata):
            raise TypeError("metadata keys must be strings")
        if not all(_is_json_value(value) for value in metadata.values()):
            raise TypeError("metadata values must be strict JSON values")

        document_ids = _normalize_id_sequence(
            self.expected_document_ids,
            field_name="expected_document_ids",
        )
        chunk_ids = _normalize_id_sequence(
            self.expected_chunk_ids,
            field_name="expected_chunk_ids",
        )
        _validate_unique_ids(document_ids, field_name="expected_document_ids")
        _validate_unique_ids(chunk_ids, field_name="expected_chunk_ids")
        if not document_ids and not chunk_ids and not self.expected_content_contains:
            raise ValueError(
                "at least one of expected_document_ids, expected_chunk_ids, "
                "or expected_content_contains must be non-empty"
            )

        object.__setattr__(self, "expected_document_ids", document_ids)
        object.__setattr__(self, "expected_chunk_ids", chunk_ids)
        object.__setattr__(
            self,
            "expected_answer_contains",
            _normalize_optional_strings(
                self.expected_answer_contains,
                field_name="expected_answer_contains",
            ),
        )
        if self.expected_content_contains is not None:
            object.__setattr__(
                self,
                "expected_content_contains",
                self.expected_content_contains.strip(),
            )
        object.__setattr__(self, "metadata", metadata)

    @property
    def document_ids(self) -> tuple[str, ...]:
        """Return the normalized expected document IDs."""

        return _normalize_id_sequence(
            self.expected_document_ids,
            field_name="expected_document_ids",
        )

    @property
    def chunk_ids(self) -> tuple[str, ...]:
        """Return the normalized expected chunk IDs."""

        return _normalize_id_sequence(
            self.expected_chunk_ids,
            field_name="expected_chunk_ids",
        )

    @property
    def answer_fragments(self) -> tuple[str, ...] | None:
        """Return the normalized answer fragments, or None when undeclared."""

        return _normalize_optional_strings(
            self.expected_answer_contains,
            field_name="expected_answer_contains",
        )

    def to_dict(self) -> dict[str, JSONValue]:
        """Return the stable JSON object used by the RAG JSONL contract."""

        return {
            "case_id": self.case_id,
            "query": self.query,
            "expected_document_ids": list(self.document_ids),
            "expected_chunk_ids": list(self.chunk_ids),
            "expected_answer_contains": (
                None if self.answer_fragments is None else list(self.answer_fragments)
            ),
            "expected_content_contains": self.expected_content_contains,
            "metadata": dict(self.metadata),
            "top_k": self.top_k,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> RAGEvalCase:
        """Construct a case from one validated JSON object."""

        allowed = {
            "case_id",
            "query",
            "expected_document_ids",
            "expected_chunk_ids",
            "expected_answer_contains",
            "expected_content_contains",
            "metadata",
            "top_k",
        }
        unknown = set(payload) - allowed
        if unknown:
            names = ", ".join(sorted(str(name) for name in unknown))
            raise ValueError(f"unknown RAG case fields: {names}")
        missing = {"case_id", "query"} - set(payload)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"missing RAG case fields: {names}")

        case_id = payload["case_id"]
        query = payload["query"]
        if not isinstance(case_id, str):
            raise TypeError("case_id must be a string")
        if not isinstance(query, str):
            raise TypeError("query must be a string")

        metadata_value = payload.get("metadata", {})
        if not isinstance(metadata_value, Mapping):
            raise TypeError("metadata must be a JSON object")
        metadata: dict[str, JSONValue] = {}
        for key, value in metadata_value.items():
            if not isinstance(key, str) or not _is_json_value(value):
                raise TypeError("metadata must contain only strict JSON values")
            metadata[key] = value

        raw_content_contains = payload.get("expected_content_contains")
        top_k_value = payload.get("top_k")
        if top_k_value is not None and (
            not isinstance(top_k_value, int) or isinstance(top_k_value, bool)
        ):
            raise TypeError("top_k must be an integer or null")

        return cls(
            case_id=case_id,
            query=query,
            expected_document_ids=_parse_string_sequence(
                payload.get("expected_document_ids"),
                field_name="expected_document_ids",
            ),
            expected_chunk_ids=_parse_string_sequence(
                payload.get("expected_chunk_ids"),
                field_name="expected_chunk_ids",
            ),
            expected_answer_contains=_parse_string_sequence(
                payload.get("expected_answer_contains"),
                field_name="expected_answer_contains",
            ),
            expected_content_contains=(
                raw_content_contains if isinstance(raw_content_contains, str) else None
            ),
            metadata=metadata,
            top_k=top_k_value,
        )


@dataclass(frozen=True)
class RetrievalReference:
    """Stable retrieval metadata used by deterministic RAG metrics.

    ``content`` is optional: it feeds content-substring expectations
    (``expected_content_contains``) and is never required for ID-based
    metrics.
    """

    document_id: str
    chunk_id: str
    chunk_index: int
    distance: float
    content: str | None = None

    def __post_init__(self) -> None:
        """Reject malformed retrieval observations early."""

        if not isinstance(self.document_id, str) or not self.document_id.strip():
            raise ValueError("document_id must not be empty")
        if not isinstance(self.chunk_id, str) or not self.chunk_id.strip():
            raise ValueError("chunk_id must not be empty")
        if (
            not isinstance(self.chunk_index, int)
            or isinstance(self.chunk_index, bool)
            or self.chunk_index < 0
        ):
            raise ValueError("chunk_index must be a non-negative integer")
        if (
            isinstance(self.distance, bool)
            or not isinstance(self.distance, (int, float))
            or not math.isfinite(self.distance)
            or self.distance < 0
        ):
            raise ValueError("distance must be a finite non-negative number")


@dataclass(frozen=True)
class RetrievalOutcome:
    """Normalized retrieval observation returned by an injected retriever."""

    references: tuple[RetrievalReference, ...] = ()
    status: str = "success"
    error: str | None = None
    latency_ms: float | None = None

    def __post_init__(self) -> None:
        """Validate outcome fields and normalize the reference sequence."""

        if not isinstance(self.status, str) or not self.status.strip():
            raise ValueError("status must not be empty")
        if self.error is not None and (
            not isinstance(self.error, str) or not self.error.strip()
        ):
            raise ValueError("error must be a non-empty string when provided")
        if self.latency_ms is not None:
            if (
                isinstance(self.latency_ms, bool)
                or not isinstance(self.latency_ms, (int, float))
                or not math.isfinite(self.latency_ms)
                or self.latency_ms < 0
            ):
                raise ValueError("latency_ms must be a finite non-negative number")
        references = tuple(self.references)
        if any(
            not isinstance(reference, RetrievalReference) for reference in references
        ):
            raise TypeError("references must contain RetrievalReference values")
        object.__setattr__(self, "references", references)


@dataclass(frozen=True)
class RAGExecution:
    """Optional runtime observation that pairs retrieval with an answer."""

    retrieval: RetrievalOutcome
    answer: str | None = None

    def __post_init__(self) -> None:
        """Reject malformed optional answer observations."""

        if not isinstance(self.retrieval, RetrievalOutcome):
            raise TypeError("retrieval must be a RetrievalOutcome")
        if self.answer is not None and not isinstance(self.answer, str):
            raise TypeError("answer must be a string or None")


@dataclass(frozen=True)
class RAGEvalCaseResult:
    """Evaluation outcome and observations for one RAG golden case."""

    case_id: str
    status: str
    success: bool
    expected_document_ids: tuple[str, ...]
    expected_chunk_ids: tuple[str, ...]
    retrieved_document_ids: tuple[str, ...]
    retrieved_chunk_ids: tuple[str, ...]
    retrieved_count: int
    document_recall_at_k: float | None
    chunk_recall_at_k: float | None
    context_recall_at_k: float | None
    document_mrr_at_k: float | None
    chunk_mrr_at_k: float | None
    context_mrr_at_k: float | None
    content_mrr_at_k: float | None
    answer_correct: bool | None
    top_k: int | None
    latency_ms: float
    error: str | None = None
    expected_content_contains: str | None = None
    content_hit: bool | None = None

    def to_dict(self) -> dict[str, JSONValue]:
        """Return the stable JSON object used by RAG evaluation reports."""

        return {
            "case_id": self.case_id,
            "status": self.status,
            "success": self.success,
            "expected_document_ids": list(self.expected_document_ids),
            "expected_chunk_ids": list(self.expected_chunk_ids),
            "expected_content_contains": self.expected_content_contains,
            "content_hit": self.content_hit,
            "retrieved_document_ids": list(self.retrieved_document_ids),
            "retrieved_chunk_ids": list(self.retrieved_chunk_ids),
            "retrieved_count": self.retrieved_count,
            "document_recall_at_k": self.document_recall_at_k,
            "chunk_recall_at_k": self.chunk_recall_at_k,
            "context_recall_at_k": self.context_recall_at_k,
            "document_mrr_at_k": self.document_mrr_at_k,
            "chunk_mrr_at_k": self.chunk_mrr_at_k,
            "context_mrr_at_k": self.context_mrr_at_k,
            "content_mrr_at_k": self.content_mrr_at_k,
            "answer_correct": self.answer_correct,
            "top_k": self.top_k,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


@dataclass(frozen=True)
class RAGSummary:
    """Aggregate metrics for one RAG evaluation batch."""

    case_count: int
    retrieval_success_count: int
    retrieval_success_rate: float
    context_recall_at_k: float | None
    document_recall_at_k: float | None
    chunk_recall_at_k: float | None
    answer_correctness_accuracy: float | None
    answer_correctness_case_count: int
    average_retrieved_chunks: float
    p95_latency_ms: float
    document_mrr_at_k: float | None
    context_mrr_at_k: float | None
    content_mrr_at_k: float | None
    content_hit_rate: float | None = None
    content_expected_count: int = 0

    def to_dict(self) -> dict[str, JSONValue]:
        """Return the stable JSON object used by RAG evaluation reports."""

        return {
            "case_count": self.case_count,
            "retrieval_success_count": self.retrieval_success_count,
            "retrieval_success_rate": self.retrieval_success_rate,
            "context_recall_at_k": self.context_recall_at_k,
            "document_recall_at_k": self.document_recall_at_k,
            "chunk_recall_at_k": self.chunk_recall_at_k,
            "answer_correctness_accuracy": self.answer_correctness_accuracy,
            "answer_correctness_case_count": self.answer_correctness_case_count,
            "average_retrieved_chunks": self.average_retrieved_chunks,
            "p95_latency_ms": self.p95_latency_ms,
            "document_mrr_at_k": self.document_mrr_at_k,
            "context_mrr_at_k": self.context_mrr_at_k,
            "content_mrr_at_k": self.content_mrr_at_k,
            "content_hit_rate": self.content_hit_rate,
            "content_expected_count": self.content_expected_count,
        }


@dataclass(frozen=True)
class RAGReport:
    """Ordered case results together with aggregate RAG metrics."""

    results: tuple[RAGEvalCaseResult, ...]
    summary: RAGSummary

    @property
    def case_results(self) -> tuple[RAGEvalCaseResult, ...]:
        """Return the ordered case results."""

        return self.results

    def to_dict(self) -> dict[str, JSONValue]:
        """Return the stable JSON object used by RAG evaluation reports."""

        return {
            "results": [result.to_dict() for result in self.results],
            "summary": self.summary.to_dict(),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize the report with strict, stable JSON."""

        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            indent=indent,
        )
