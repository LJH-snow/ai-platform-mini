"""Database models for RAG evaluation run persistence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class RAGEvaluationRunTable(Base):
    __tablename__ = "rag_evaluation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(512), nullable=False)
    retriever: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_success_rate: Mapped[float] = mapped_column(Float, nullable=False)
    context_recall_at_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    document_recall_at_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    chunk_recall_at_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    answer_correctness_accuracy: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    answer_correctness_case_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    average_retrieved_chunks: Mapped[float] = mapped_column(Float, nullable=False)
    p95_latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
