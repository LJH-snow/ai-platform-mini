"""Persist safe multi-agent run projections for tenant-scoped history replay."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import APIKey
from app.core.context import RequestContext
from app.db.models import MultiAgentRunEventTable, MultiAgentRunRecordTable
from app.multi_agent.events import MultiAgentEvent
from app.multi_agent.models import OrchestrationResult, OrchestrationStatus

logger = logging.getLogger(__name__)

MAX_MULTI_AGENT_EVENTS_PER_RUN = 1000
MAX_MULTI_AGENT_EVENT_PAYLOAD_BYTES = 2 * 1024 * 1024


class MultiAgentRunRecordService:
    """Store and query safe multi-agent run projections per tenant."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(
        self,
        result: OrchestrationResult,
        *,
        context: RequestContext,
        api_key: APIKey,
        request_id: str,
        status: OrchestrationStatus,
        final_output: str = "",
        error_code: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        """Persist one run; the payload holds only the safe public projection."""
        payload = _project_payload(
            result=result,
            final_output=final_output,
            error_code=error_code,
        )
        identity = context.identity
        workspace_id = identity.workspace_id if identity else None
        row = MultiAgentRunRecordTable(
            run_id=result.run_id,
            request_id=request_id,
            api_key_hash=api_key.key,
            api_key_name=api_key.name,
            workspace_id=workspace_id,
            status=status.value,
            stop_reason=_stop_reason(status),
            started_at=started_at or result.started_at,
            completed_at=result.completed_at,
            duration_ms=result.duration_ms,
            total_tokens=result.total_token_usage,
            payload=payload,
        )
        async with self._session_factory() as session:
            await session.merge(row)
            await session.commit()

    async def list_runs(
        self,
        limit: int = 50,
        *,
        owner_scope: str | None = None,
    ) -> list[MultiAgentRunRecordTable]:
        """List runs, optionally scoped to one tenant owner.

        Matches workspace-bound rows by workspace_id and legacy rows
        (workspace_id IS NULL) by api_key_hash, exactly like agent runs.
        """
        async with self._session_factory() as session:
            stmt = select(MultiAgentRunRecordTable).order_by(
                desc(MultiAgentRunRecordTable.started_at),
                desc(MultiAgentRunRecordTable.created_at),
            )
            if owner_scope is not None:
                stmt = stmt.where(
                    or_(
                        MultiAgentRunRecordTable.workspace_id == owner_scope,
                        and_(
                            MultiAgentRunRecordTable.workspace_id.is_(None),
                            MultiAgentRunRecordTable.api_key_hash == owner_scope,
                        ),
                    )
                )
            result = await session.scalars(stmt.limit(limit))
            return list(result)

    async def get_run(
        self, run_id: str, *, owner_scope: str | None = None
    ) -> MultiAgentRunRecordTable | None:
        """Fetch one run; with ``owner_scope`` cross-tenant reads return None."""
        async with self._session_factory() as session:
            row = await session.get(MultiAgentRunRecordTable, run_id)
            if row is None or owner_scope is None:
                return row
            if row.workspace_id == owner_scope:
                return row
            if row.workspace_id is None and row.api_key_hash == owner_scope:
                return row
            return None

    async def save_event(self, event: MultiAgentEvent) -> None:
        """Persist one safe public event for the run timeline."""
        event_dict = event.to_public_dict()
        row = MultiAgentRunEventTable(
            run_id=event.run_id,
            sequence=event.sequence,
            kind=event.kind.value,
            occurred_at=event.occurred_at,
            task_id=event.task_id,
            agent_role=event.agent_role,
            agent_event_kind=event.agent_event_kind,
            step_index=event.step_index,
            duration_ms=event.duration_ms,
            token_usage=event.token_usage,
            output_summary=event.output_summary,
            error_code=event.error_code,
            tool_name=event.tool_name,
            call_id=event.call_id,
            reasoning=event.reasoning,
            final_output=event.final_output,
            total_token_usage=event.total_token_usage,
            subtasks=(
                event_dict.get("subtasks") if event_dict.get("subtasks") else None
            ),
            subtask_results=(
                event_dict.get("subtask_results")
                if event_dict.get("subtask_results")
                else None
            ),
            payload=event_dict,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()

    async def list_events(
        self, run_id: str, *, limit: int = 1000
    ) -> list[MultiAgentRunEventTable]:
        """Fetch persisted events for one run in sequence order."""
        stmt = (
            select(MultiAgentRunEventTable)
            .where(MultiAgentRunEventTable.run_id == run_id)
            .order_by(MultiAgentRunEventTable.sequence)
            .limit(min(max(limit, 1), MAX_MULTI_AGENT_EVENTS_PER_RUN))
        )
        async with self._session_factory() as session:
            result = await session.scalars(stmt)
            return list(result)


class MultiAgentEventSink(Protocol):
    """Write side for persisted multi-agent events."""

    async def save_event(self, event: MultiAgentEvent) -> None: ...


class MultiAgentEventRecorder:
    """Best-effort observer persisting the safe event log for one run.

    The cap protects against unbounded answer_delta growth; after the cap is
    reached the recorder stops writing and logs the skipped tail.
    """

    def __init__(self, record_service: MultiAgentEventSink) -> None:
        self._record_service = record_service
        self._count = 0
        self._bytes = 0

    async def on_event(self, event: MultiAgentEvent) -> None:
        if self._count >= MAX_MULTI_AGENT_EVENTS_PER_RUN:
            logger.warning(
                "multi_agent_event_recorder_cap run_id=%s skipped_kind=%s",
                event.run_id,
                event.kind.value,
            )
            return
        event_dict = event.to_public_dict()
        payload_bytes = len(json.dumps(event_dict, ensure_ascii=False))
        if self._bytes + payload_bytes > MAX_MULTI_AGENT_EVENT_PAYLOAD_BYTES:
            logger.warning(
                "multi_agent_event_recorder_payload_cap run_id=%s skipped_kind=%s",
                event.run_id,
                event.kind.value,
            )
            return
        try:
            await self._record_service.save_event(event)
            self._count += 1
            self._bytes += payload_bytes
        except Exception:
            logger.exception(
                "multi_agent_event_recorder_failed run_id=%s kind=%s",
                event.run_id,
                event.kind.value,
            )


def _stop_reason(status: OrchestrationStatus) -> str:
    """Map a run status to the public stop-reason column."""
    return {
        OrchestrationStatus.COMPLETED: "completed",
        OrchestrationStatus.FAILED: "failed",
        OrchestrationStatus.CANCELLED: "cancelled",
        OrchestrationStatus.TIMED_OUT: "timed_out",
        OrchestrationStatus.BUDGET_EXCEEDED: "budget_exceeded",
    }.get(status, "unknown")


def public_run_payload(row: MultiAgentRunRecordTable) -> Mapping[str, Any]:
    """Build a JSON-safe projection without the raw API key hash."""
    return {
        "run_id": row.run_id,
        "request_id": row.request_id,
        "api_key_prefix": row.api_key_hash[:8],
        "api_key_name": row.api_key_name,
        "status": row.status,
        "stop_reason": row.stop_reason,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "duration_ms": row.duration_ms,
        "total_tokens": row.total_tokens,
        "response": row.payload,
    }


def _project_payload(
    *,
    result: OrchestrationResult,
    final_output: str,
    error_code: str | None,
) -> dict[str, object]:
    """Build the safe payload from a finished orchestration result."""
    return {
        "status": result.status.value,
        "final_output": final_output,
        "error_code": error_code,
        "total_token_usage": result.total_token_usage,
        "duration_ms": result.duration_ms,
        "subtask_results": [
            {
                "task_id": r.task_id,
                "status": r.status.value,
                "agent_role": r.agent_role.value,
                "output": r.output,
                "error_code": (
                    "subtask_failed" if r.status.value == "failed" else None
                ),
                "token_usage": r.token_usage,
                "duration_ms": r.duration_ms,
            }
            for r in result.subtask_results
        ],
    }


def _as_list(value: object) -> list[Mapping[str, object]]:
    return (
        [item for item in value if isinstance(item, Mapping)]
        if isinstance(value, list)
        else []
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float(value: object) -> float | None:
    return (
        value
        if isinstance(value, int | float) and not isinstance(value, bool)
        else None
    )


def _optional_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def public_run_summary(payload: Mapping[str, Any]) -> dict[str, object]:
    """Tenant-safe run summary for the history list."""
    response = payload.get("response")
    subtask_count = (
        len(_as_list(response.get("subtask_results")))
        if isinstance(response, Mapping)
        else 0
    )
    return {
        "run_id": str(payload["run_id"]),
        "request_id": str(payload["request_id"]),
        "api_key_prefix": str(payload["api_key_prefix"]),
        "api_key_name": str(payload["api_key_name"]),
        "status": str(payload["status"]),
        "stop_reason": str(payload["stop_reason"]),
        "started_at": payload.get("started_at"),
        "completed_at": payload.get("completed_at"),
        "duration_ms": payload.get("duration_ms"),
        "total_tokens": payload.get("total_tokens"),
        "subtask_count": subtask_count,
    }


# Allowlist for the detail projection — anything else in a stored payload is
# dropped at the user-facing boundary even if it ever leaks in.
_RESPONSE_ALLOWED_KEYS = frozenset(
    {"status", "final_output", "error_code", "total_token_usage", "duration_ms"}
)
_SUBTASK_ALLOWED_KEYS = frozenset(
    {
        "task_id",
        "status",
        "agent_role",
        "output",
        "error_code",
        "token_usage",
        "duration_ms",
    }
)


def project_run_response(payload: Mapping[str, object]) -> dict[str, object]:
    """Project a stored payload onto the public allowlist for detail replay."""
    response = payload.get("response")
    if not isinstance(response, Mapping):
        response = {}
    projected: dict[str, object] = {
        key: value for key, value in response.items() if key in _RESPONSE_ALLOWED_KEYS
    }
    subtask_results = _as_list(response.get("subtask_results"))
    projected["subtask_results"] = [
        {key: value for key, value in item.items() if key in _SUBTASK_ALLOWED_KEYS}
        for item in subtask_results
    ]
    return projected
