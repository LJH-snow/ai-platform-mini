"""Plan and Subscription ORM models (Sprint E1a Billing)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class PlanTable(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Reserved for future versioned plan semantics; subscription changes
    # never rewrite historical subscriptions (backlog).
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    # NULL = inherit the global default (settings) for that dimension.
    daily_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    monthly_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # NULL = unlimited.
    max_agents: Mapped[int | None] = mapped_column(nullable=True)
    max_documents: Mapped[int | None] = mapped_column(nullable=True)
    max_members: Mapped[int | None] = mapped_column(nullable=True)
    # Feature capabilities: {"reranker": bool, "benchmark": bool, ...}.
    features: Mapped[dict[str, bool]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SubscriptionTable(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # One effective subscription per workspace.
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    plan_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("plans.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
