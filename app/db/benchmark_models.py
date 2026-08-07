"""Agent Benchmark ORM model (Sprint B)."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class AgentBenchmarkRunTable(Base):
    __tablename__ = "agent_benchmark_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_set: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_call_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    task_completion_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_steps: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    task_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metric_payload: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
