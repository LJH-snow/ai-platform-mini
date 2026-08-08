"""Audit log — actor, record model, repository protocol, dual storage."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.audit_models import AuditEventTable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditActor:
    """Who performed an audited action (resolved at the API layer).

    Services never reach into the request; the API boundary builds this
    from the identity context and passes it into the service hook.
    """

    workspace_id: str | None = None
    api_key_hash: str | None = None
    user_id: str | None = None
    ip: str | None = None


@dataclass(frozen=True)
class AuditEvent:
    """One immutable audit record."""

    action: str
    resource_type: str
    resource_id: str
    workspace_id: str | None = None
    api_key_hash: str | None = None
    user_id: str | None = None
    before: dict[str, object] | None = None
    after: dict[str, object] | None = None
    ip: str | None = None
    id: int = 0
    created_at: datetime | None = None


@runtime_checkable
class AuditRepository(Protocol):
    async def record(self, event: AuditEvent) -> AuditEvent: ...

    async def list_events(
        self,
        *,
        workspace_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> list[AuditEvent]: ...


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._id_seq = 0

    async def record(self, event: AuditEvent) -> AuditEvent:
        self._id_seq += 1
        saved = AuditEvent(
            id=self._id_seq,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            workspace_id=event.workspace_id,
            api_key_hash=event.api_key_hash,
            user_id=event.user_id,
            before=event.before,
            after=event.after,
            ip=event.ip,
            created_at=datetime.now(),
        )
        self._events.append(saved)
        return saved

    async def list_events(
        self,
        *,
        workspace_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> list[AuditEvent]:
        filtered = list(self._events)
        if workspace_id is not None:
            filtered = [e for e in filtered if e.workspace_id == workspace_id]
        if action is not None:
            filtered = [e for e in filtered if e.action == action]
        filtered.sort(key=lambda e: e.created_at or datetime.min, reverse=True)
        return filtered[:limit]


class PostgresAuditRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, event: AuditEvent) -> AuditEvent:
        row = AuditEventTable(
            workspace_id=event.workspace_id,
            api_key_hash=event.api_key_hash,
            user_id=event.user_id,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            before=event.before,
            after=event.after,
            ip=event.ip,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return _row_to_event(row)

    async def list_events(
        self,
        *,
        workspace_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> list[AuditEvent]:
        stmt = select(AuditEventTable).order_by(
            desc(AuditEventTable.created_at), desc(AuditEventTable.id)
        )
        if workspace_id is not None:
            stmt = stmt.where(AuditEventTable.workspace_id == workspace_id)
        if action is not None:
            stmt = stmt.where(AuditEventTable.action == action)
        async with self._session_factory() as session:
            rows = await session.scalars(stmt.limit(limit))
            return [_row_to_event(row) for row in rows]


def _row_to_event(row: AuditEventTable) -> AuditEvent:
    return AuditEvent(
        id=row.id,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        workspace_id=row.workspace_id,
        api_key_hash=row.api_key_hash,
        user_id=row.user_id,
        before=row.before if isinstance(row.before, dict) else None,
        after=row.after if isinstance(row.after, dict) else None,
        ip=row.ip,
        created_at=row.created_at,
    )


class AuditService:
    """Record audit events without ever breaking the business path.

    Failures are logged with ``logger.exception`` (visible, alarmable)
    instead of being swallowed silently — audit is best-effort by
    design but must remain diagnosable (review-mandated semantics).
    """

    def __init__(self, repository: AuditRepository) -> None:
        self._repo = repository

    async def record(
        self,
        *,
        action: str,
        resource_type: str,
        resource_id: str,
        actor: AuditActor | None = None,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
    ) -> None:
        try:
            await self._repo.record(
                AuditEvent(
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    workspace_id=actor.workspace_id if actor else None,
                    api_key_hash=actor.api_key_hash if actor else None,
                    user_id=actor.user_id if actor else None,
                    ip=actor.ip if actor else None,
                    before=before,
                    after=after,
                )
            )
        except Exception:
            # Best-effort audit: the business action already succeeded;
            # surface the failure loudly so operators can alarm on it.
            logger.exception(
                "audit write failed: %s %s/%s",
                action,
                resource_type,
                resource_id,
            )

    async def list_events(
        self,
        *,
        workspace_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> list[AuditEvent]:
        return await self._repo.list_events(
            workspace_id=workspace_id, action=action, limit=limit
        )
