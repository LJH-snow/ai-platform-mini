import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class APIKeyTable(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    key_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    user_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DailyUsageTable(Base):
    __tablename__ = "daily_usage"
    __table_args__ = (
        UniqueConstraint(
            "api_key_hash", "usage_date", "model", name="uq_daily_usage_key_date_model"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # F1 tenant scoping: workspace-bound usage carries the workspace id;
    # legacy rows keep NULL and match by key hash (same semantics as runs).
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class QuotaReservationTable(Base):
    __tablename__ = "quota_reservations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Workspace scope for shared quota (NULL = legacy key, key-scoped).
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settled: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AgentRunRecordTable(Base):
    __tablename__ = "agent_run_records"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    api_key_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # F1 tenant scoping for run records: workspace-bound runs carry the
    # workspace id; legacy (unbound) runs keep NULL and match by key hash.
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    stop_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[float | None] = mapped_column(nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )


class WorkspaceQuotaTable(Base):
    """Per-workspace quota overrides; NULL limits inherit the global default."""

    __tablename__ = "workspace_quotas"

    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    daily_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    monthly_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
