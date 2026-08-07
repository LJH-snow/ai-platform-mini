from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import APIKey
from app.core.context import RequestContext
from app.db.models import AgentRunRecordTable
from app.schemas.admin import AgentRunRecordSummary
from app.schemas.agent import AgentRunRequest, AgentRunResponse


class AgentRunRecordService:
    """Persist safe Agent Run projections for the administrator console."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(
        self,
        response: AgentRunResponse,
        request: AgentRunRequest,
        context: RequestContext,
        api_key: APIKey,
        model: str | None = None,
        *,
        agent_id: str | None = None,
        prompt_ref: str | None = None,
    ) -> None:
        payload: dict[str, object] = response.model_dump(mode="json")
        # Audit trail for the Agent definition that produced this run
        # (roadmap B5: record prompt name/version in the audit payload).
        payload["agent_id"] = agent_id
        payload["prompt_ref"] = prompt_ref
        identity = context.identity
        workspace_id = identity.workspace_id if identity else None
        row = AgentRunRecordTable(
            run_id=response.run_id,
            request_id=context.request_id,
            api_key_hash=api_key.key,
            api_key_name=api_key.name,
            workspace_id=workspace_id,
            model=model or request.model or "default",
            status=response.status,
            stop_reason=response.stop_reason,
            started_at=response.started_at,
            completed_at=response.completed_at,
            duration_ms=response.duration_ms,
            total_tokens=response.usage.total_tokens,
            payload=payload,
        )
        async with self._session_factory() as session:
            await session.merge(row)
            await session.commit()

    async def list_runs(
        self,
        limit: int = 50,
        status: str | None = None,
        *,
        owner_scope: str | None = None,
        agent_id: str | None = None,
    ) -> list[AgentRunRecordTable]:
        """List runs, optionally scoped to one tenant owner.

        ``owner_scope`` matches workspace-bound rows by workspace_id and
        legacy rows (workspace_id IS NULL) by api_key_hash, so both key
        generations see exactly their own runs.
        """
        async with self._session_factory() as session:
            stmt = select(AgentRunRecordTable).order_by(
                desc(AgentRunRecordTable.started_at),
                desc(AgentRunRecordTable.created_at),
            )
            if status is not None:
                stmt = stmt.where(AgentRunRecordTable.status == status)
            if owner_scope is not None:
                stmt = stmt.where(
                    or_(
                        AgentRunRecordTable.workspace_id == owner_scope,
                        and_(
                            AgentRunRecordTable.workspace_id.is_(None),
                            AgentRunRecordTable.api_key_hash == owner_scope,
                        ),
                    )
                )
            if agent_id is not None:
                stmt = stmt.where(
                    AgentRunRecordTable.payload["agent_id"].as_string() == agent_id
                )
            result = await session.scalars(stmt.limit(limit))
            return list(result)

    async def get_run(
        self, run_id: str, *, owner_scope: str | None = None
    ) -> AgentRunRecordTable | None:
        """Fetch one run; with ``owner_scope`` cross-tenant reads return None."""
        async with self._session_factory() as session:
            row = await session.get(AgentRunRecordTable, run_id)
            if row is None or owner_scope is None:
                return row
            if row.workspace_id == owner_scope:
                return row
            if row.workspace_id is None and row.api_key_hash == owner_scope:
                return row
            return None


def public_run_payload(row: AgentRunRecordTable) -> Mapping[str, Any]:
    """Build a JSON-safe admin projection without the raw API key hash."""

    return {
        "run_id": row.run_id,
        "request_id": row.request_id,
        "api_key_prefix": row.api_key_hash[:8],
        "api_key_name": row.api_key_name,
        "model": row.model,
        "status": row.status,
        "stop_reason": row.stop_reason,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "duration_ms": row.duration_ms,
        "total_tokens": row.total_tokens,
        "response": row.payload,
    }


def public_run_summary(payload: Mapping[str, Any]) -> AgentRunRecordSummary:
    """Build the tenant-safe run summary projection (shared by admin + user APIs)."""

    response = payload.get("response")
    if not isinstance(response, dict):
        response = {}
    tool_count = sum(
        len(step.get("tool_calls", []) or [])
        for step in response.get("steps", [])
        if isinstance(step, dict)
    )
    rag_reference_count = 0
    for step in response.get("steps", []):
        if not isinstance(step, dict):
            continue
        for tool in step.get("tool_calls", []) or []:
            if not isinstance(tool, dict):
                continue
            rag = tool.get("rag")
            if isinstance(rag, dict):
                references = rag.get("references", [])
                if isinstance(references, list):
                    rag_reference_count += len(references)
    return AgentRunRecordSummary(
        run_id=str(payload["run_id"]),
        request_id=str(payload["request_id"]),
        api_key_prefix=str(payload["api_key_prefix"]),
        api_key_name=str(payload["api_key_name"]),
        model=str(payload["model"]),
        status=str(payload["status"]),
        stop_reason=str(payload["stop_reason"]),
        started_at=payload.get("started_at"),
        completed_at=payload.get("completed_at"),
        duration_ms=payload.get("duration_ms"),
        total_tokens=payload.get("total_tokens"),
        tool_count=tool_count,
        rag_reference_count=rag_reference_count,
    )
