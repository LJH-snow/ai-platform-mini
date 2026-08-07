"""Agent and Tool ORM models for Agent Definition + Tool Center (Sprint B)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class ToolTable(Base):
    __tablename__ = "tools"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    parameters_schema: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    enabled_by_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    owner: Mapped[str] = mapped_column(String(32), nullable=False, default="builtin")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentTable(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_ref: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentToolTable(Base):
    __tablename__ = "agent_tools"
    __table_args__ = (UniqueConstraint("agent_id", "tool_name", name="uq_agent_tool"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(
        String(128), ForeignKey("tools.name", ondelete="CASCADE"), nullable=False
    )


class WorkspaceToolTable(Base):
    """Per-workspace tool enablement overrides (Sprint B Tool Center).

    Absent rows mean the workspace inherits ``ToolTable.enabled_by_default``;
    present rows override it for exactly one workspace.
    """

    __tablename__ = "workspace_tools"
    __table_args__ = (
        UniqueConstraint("workspace_id", "tool_name", name="uq_workspace_tool"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(
        String(128), ForeignKey("tools.name", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
