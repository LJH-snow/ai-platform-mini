"""Repository boundary for agent benchmark run persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.benchmark_models import AgentBenchmarkRunTable


@dataclass
class BenchmarkRunRecord:
    """One persisted agent benchmark evaluation."""

    agent_id: str
    workspace_id: str
    task_set: str
    tool_call_accuracy: float | None = None
    task_completion_rate: float | None = None
    average_steps: float | None = None
    average_latency_ms: float | None = None
    task_count: int = 0
    completed_count: int = 0
    metric_payload: dict[str, object] = field(default_factory=dict)
    id: int = 0
    created_at: datetime | None = None


class BenchmarkRunRepository(Protocol):
    async def save(self, record: BenchmarkRunRecord) -> BenchmarkRunRecord: ...

    async def list_runs(
        self,
        workspace_id: str,
        *,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[BenchmarkRunRecord]: ...


class InMemoryBenchmarkRunRepository:
    """Process-local storage for tests and single-worker development."""

    def __init__(self) -> None:
        self._runs: list[BenchmarkRunRecord] = []
        self._id_seq = 0

    async def save(self, record: BenchmarkRunRecord) -> BenchmarkRunRecord:
        self._id_seq += 1
        saved = BenchmarkRunRecord(
            id=self._id_seq,
            agent_id=record.agent_id,
            workspace_id=record.workspace_id,
            task_set=record.task_set,
            tool_call_accuracy=record.tool_call_accuracy,
            task_completion_rate=record.task_completion_rate,
            average_steps=record.average_steps,
            average_latency_ms=record.average_latency_ms,
            task_count=record.task_count,
            completed_count=record.completed_count,
            metric_payload=dict(record.metric_payload),
            created_at=datetime.now(),
        )
        self._runs.append(saved)
        return saved

    async def list_runs(
        self,
        workspace_id: str,
        *,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[BenchmarkRunRecord]:
        filtered = [r for r in self._runs if r.workspace_id == workspace_id]
        if agent_id is not None:
            filtered = [r for r in filtered if r.agent_id == agent_id]
        filtered.sort(key=lambda r: r.created_at or datetime.min, reverse=True)
        return filtered[:limit]


class PostgresBenchmarkRunRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, record: BenchmarkRunRecord) -> BenchmarkRunRecord:
        row = AgentBenchmarkRunTable(
            agent_id=record.agent_id,
            workspace_id=record.workspace_id,
            task_set=record.task_set,
            tool_call_accuracy=record.tool_call_accuracy,
            task_completion_rate=record.task_completion_rate,
            average_steps=record.average_steps,
            average_latency_ms=record.average_latency_ms,
            task_count=record.task_count,
            completed_count=record.completed_count,
            metric_payload=record.metric_payload,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return _row_to_record(row)

    async def list_runs(
        self,
        workspace_id: str,
        *,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[BenchmarkRunRecord]:
        stmt = (
            select(AgentBenchmarkRunTable)
            .where(AgentBenchmarkRunTable.workspace_id == workspace_id)
            .order_by(
                desc(AgentBenchmarkRunTable.created_at),
                desc(AgentBenchmarkRunTable.id),
            )
        )
        if agent_id is not None:
            stmt = stmt.where(AgentBenchmarkRunTable.agent_id == agent_id)
        async with self._session_factory() as session:
            result = await session.scalars(stmt.limit(limit))
            return [_row_to_record(row) for row in result]


def _row_to_record(row: AgentBenchmarkRunTable) -> BenchmarkRunRecord:
    return BenchmarkRunRecord(
        id=row.id,
        agent_id=row.agent_id,
        workspace_id=row.workspace_id,
        task_set=row.task_set,
        tool_call_accuracy=row.tool_call_accuracy,
        task_completion_rate=row.task_completion_rate,
        average_steps=row.average_steps,
        average_latency_ms=row.average_latency_ms,
        task_count=row.task_count,
        completed_count=row.completed_count,
        metric_payload=(
            row.metric_payload if isinstance(row.metric_payload, dict) else {}
        ),
        created_at=row.created_at,
    )
