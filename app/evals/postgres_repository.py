"""PostgreSQL-backed RAG evaluation run repository."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.eval_models import RAGEvaluationRunTable
from app.evals.repository import RAGEvaluationRun


class PostgresRAGEvaluationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, run: RAGEvaluationRun) -> RAGEvaluationRun:
        now = datetime.now(UTC)
        row = RAGEvaluationRunTable(
            id=run.id,
            dataset=run.dataset,
            retriever=run.retriever,
            model=run.model,
            case_count=run.case_count,
            retrieval_success_rate=run.retrieval_success_rate,
            context_recall_at_k=run.context_recall_at_k,
            document_recall_at_k=run.document_recall_at_k,
            chunk_recall_at_k=run.chunk_recall_at_k,
            answer_correctness_accuracy=run.answer_correctness_accuracy,
            answer_correctness_case_count=run.answer_correctness_case_count,
            average_retrieved_chunks=run.average_retrieved_chunks,
            p95_latency_ms=run.p95_latency_ms,
            created_at=now,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            return _replace_run_created_at(run, now)

    async def list_recent(self, limit: int = 20) -> tuple[RAGEvaluationRun, ...]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(RAGEvaluationRunTable)
                .order_by(RAGEvaluationRunTable.created_at.desc())
                .limit(limit)
            )
            return tuple(_row_to_run(row) for row in rows)


def _row_to_run(row: RAGEvaluationRunTable) -> RAGEvaluationRun:
    return RAGEvaluationRun(
        id=row.id,
        dataset=row.dataset,
        retriever=row.retriever,
        model=row.model,
        case_count=row.case_count,
        retrieval_success_rate=row.retrieval_success_rate,
        context_recall_at_k=row.context_recall_at_k,
        document_recall_at_k=row.document_recall_at_k,
        chunk_recall_at_k=row.chunk_recall_at_k,
        answer_correctness_accuracy=row.answer_correctness_accuracy,
        answer_correctness_case_count=row.answer_correctness_case_count,
        average_retrieved_chunks=row.average_retrieved_chunks,
        p95_latency_ms=row.p95_latency_ms,
        created_at=row.created_at,
    )


def _replace_run_created_at(
    run: RAGEvaluationRun, created_at: datetime
) -> RAGEvaluationRun:
    return replace(run, created_at=created_at)
