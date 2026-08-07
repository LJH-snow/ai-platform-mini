"""Database model for persistent PDF report workflow run metadata."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class WorkflowRunTable(Base):
    __tablename__ = "workflow_runs"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    report_topic: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
