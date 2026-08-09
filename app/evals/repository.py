"""Repository boundary for RAG evaluation run persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class RAGEvaluationRun:
    id: str
    dataset: str
    retriever: str
    model: str | None
    case_count: int
    retrieval_success_rate: float
    context_recall_at_k: float | None
    document_recall_at_k: float | None
    chunk_recall_at_k: float | None
    context_mrr_at_k: float | None
    answer_correctness_accuracy: float | None
    answer_correctness_case_count: int
    average_retrieved_chunks: float
    p95_latency_ms: float
    created_at: datetime | None = None


class RAGEvaluationRepository(Protocol):
    async def save(self, run: RAGEvaluationRun) -> RAGEvaluationRun: ...

    async def list_recent(self, limit: int = 20) -> tuple[RAGEvaluationRun, ...]: ...
