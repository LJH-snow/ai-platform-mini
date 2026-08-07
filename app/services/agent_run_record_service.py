from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import APIKey
from app.core.context import RequestContext
from app.db.models import AgentRunRecordTable
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
        row = AgentRunRecordTable(
            run_id=response.run_id,
            request_id=context.request_id,
            api_key_hash=api_key.key,
            api_key_name=api_key.name,
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
    ) -> list[AgentRunRecordTable]:
        async with self._session_factory() as session:
            stmt = select(AgentRunRecordTable).order_by(
                desc(AgentRunRecordTable.started_at),
                desc(AgentRunRecordTable.created_at),
            )
            if status is not None:
                stmt = stmt.where(AgentRunRecordTable.status == status)
            result = await session.scalars(stmt.limit(limit))
            return list(result)

    async def get_run(self, run_id: str) -> AgentRunRecordTable | None:
        async with self._session_factory() as session:
            return await session.get(AgentRunRecordTable, run_id)


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
