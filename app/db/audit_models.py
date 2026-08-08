"""Audit event ORM model (Sprint E1b)."""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class AuditEventTable(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_workspace_created", "workspace_id", "created_at"),
        Index("ix_audit_events_action", "action"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
