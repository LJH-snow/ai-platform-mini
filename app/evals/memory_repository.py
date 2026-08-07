"""In-memory RAG evaluation run repository for tests and local development."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from app.evals.repository import RAGEvaluationRun


class InMemoryRAGEvaluationRepository:
    def __init__(self) -> None:
        self._runs: list[RAGEvaluationRun] = []

    async def save(self, run: RAGEvaluationRun) -> RAGEvaluationRun:
        now = datetime.now(UTC)
        saved = replace(run, created_at=now)
        self._runs.append(saved)
        return saved

    async def list_recent(self, limit: int = 20) -> tuple[RAGEvaluationRun, ...]:
        return tuple(self._runs[-limit:][::-1])
