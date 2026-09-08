"""Persistence service for multi-agent orchestration canvas configurations."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import ColumnElement

from app.auth.models import APIKey
from app.core.context import RequestContext
from app.db.models import MultiAgentConfigTable

logger = logging.getLogger(__name__)


class MultiAgentConfigService:
    """CRUD for canvas configurations, scoped per tenant."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        name: str,
        dag_json: dict[str, Any],
        orchestration_config: dict[str, Any],
        description: str | None = None,
        context: RequestContext,
        api_key: APIKey,
    ) -> MultiAgentConfigTable:
        identity = context.identity
        workspace_id = identity.workspace_id if identity else None
        row = MultiAgentConfigTable(
            workspace_id=workspace_id,
            name=name,
            description=description,
            dag_json=dag_json,
            orchestration_config=orchestration_config,
            created_by=identity.user_id if identity else None,
        )
        async with self._session_factory() as session:
            session.add(row)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            await session.refresh(row)
            return row

    async def list_configs(
        self,
        *,
        owner_scope: str | None = None,
        limit: int = 50,
    ) -> list[MultiAgentConfigTable]:
        stmt = select(MultiAgentConfigTable)
        stmt = stmt.where(_owner_filter(owner_scope))
        stmt = stmt.order_by(MultiAgentConfigTable.updated_at.desc()).limit(limit)
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_config(
        self,
        config_id: str,
        *,
        owner_scope: str | None = None,
    ) -> MultiAgentConfigTable | None:
        stmt = select(MultiAgentConfigTable).where(
            and_(
                MultiAgentConfigTable.id == config_id,
                _owner_filter(owner_scope),
            )
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_config(
        self,
        config_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        dag_json: dict[str, Any] | None = None,
        orchestration_config: dict[str, Any] | None = None,
        owner_scope: str | None = None,
    ) -> MultiAgentConfigTable | None:
        async with self._session_factory() as session:
            stmt = select(MultiAgentConfigTable).where(
                and_(
                    MultiAgentConfigTable.id == config_id,
                    _owner_filter(owner_scope),
                )
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            if name is not None:
                row.name = name
            if description is not None:
                row.description = description
            if dag_json is not None:
                row.dag_json = dag_json
            if orchestration_config is not None:
                row.orchestration_config = orchestration_config
            row.version = (row.version or 1) + 1
            await session.commit()
            await session.refresh(row)
            return row

    async def delete_config(
        self,
        config_id: str,
        *,
        owner_scope: str | None = None,
    ) -> bool:
        async with self._session_factory() as session:
            stmt = select(MultiAgentConfigTable).where(
                and_(
                    MultiAgentConfigTable.id == config_id,
                    _owner_filter(owner_scope),
                )
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True


def _owner_filter(owner_scope: str | None) -> ColumnElement[bool]:
    if owner_scope is None:
        return MultiAgentConfigTable.workspace_id.is_(None)
    return or_(
        MultiAgentConfigTable.workspace_id == owner_scope,
        MultiAgentConfigTable.workspace_id.is_(None),
    )
