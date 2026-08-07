"""Agent Definition repository protocol and implementations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_config.models import (
    AgentRecord,
    AgentToolRecord,
    ToolRecord,
    WorkspaceToolRecord,
)
from app.db.agent_models import (
    AgentTable,
    AgentToolTable,
    ToolTable,
    WorkspaceToolTable,
)


@runtime_checkable
class AgentDefinitionRepository(Protocol):
    # ── Agents ────────────────────────────────────────────────────────────
    async def create_agent(self, record: AgentRecord) -> AgentRecord: ...
    async def find_agent_by_id(self, agent_id: str) -> AgentRecord | None: ...
    async def list_agents(self, workspace_id: str) -> list[AgentRecord]: ...
    async def update_agent(self, record: AgentRecord) -> AgentRecord | None: ...
    async def delete_agent(self, agent_id: str) -> bool: ...
    # ── Agent tools ───────────────────────────────────────────────────────
    async def set_agent_tools(
        self, agent_id: str, tool_names: list[str]
    ) -> list[AgentToolRecord]: ...
    async def get_agent_tools(self, agent_id: str) -> list[AgentToolRecord]: ...
    # ── Tools (registry) ─────────────────────────────────────────────────
    async def seed_tool(self, record: ToolRecord) -> ToolRecord: ...
    async def list_tools(self) -> list[ToolRecord]: ...
    # ── Workspace tool enablement ────────────────────────────────────────
    async def set_workspace_tool(
        self, workspace_id: str, tool_name: str, enabled: bool
    ) -> WorkspaceToolRecord: ...
    async def list_workspace_tools(
        self, workspace_id: str
    ) -> list[WorkspaceToolRecord]: ...
    async def get_workspace_tool(
        self, workspace_id: str, tool_name: str
    ) -> WorkspaceToolRecord | None: ...


# ── In-memory ────────────────────────────────────────────────────────────────


class InMemoryAgentDefinitionRepository:
    def __init__(self) -> None:
        self._agents: dict[str, AgentRecord] = {}
        self._agent_tools: list[AgentToolRecord] = []
        self._tools: dict[str, ToolRecord] = {}
        self._workspace_tools: list[WorkspaceToolRecord] = []
        self._tool_id_seq = 0

    async def create_agent(self, record: AgentRecord) -> AgentRecord:
        self._agents[record.id] = record
        return record

    async def find_agent_by_id(self, agent_id: str) -> AgentRecord | None:
        return self._agents.get(agent_id)

    async def list_agents(self, workspace_id: str) -> list[AgentRecord]:
        return [a for a in self._agents.values() if a.workspace_id == workspace_id]

    async def update_agent(self, record: AgentRecord) -> AgentRecord | None:
        if record.id not in self._agents:
            return None
        self._agents[record.id] = record
        return record

    async def delete_agent(self, agent_id: str) -> bool:
        return self._agents.pop(agent_id, None) is not None

    async def set_agent_tools(
        self, agent_id: str, tool_names: list[str]
    ) -> list[AgentToolRecord]:
        self._agent_tools = [t for t in self._agent_tools if t.agent_id != agent_id]
        records: list[AgentToolRecord] = []
        for name in tool_names:
            self._tool_id_seq += 1
            at = AgentToolRecord(
                id=self._tool_id_seq, agent_id=agent_id, tool_name=name
            )
            self._agent_tools.append(at)
            records.append(at)
        return records

    async def get_agent_tools(self, agent_id: str) -> list[AgentToolRecord]:
        return [t for t in self._agent_tools if t.agent_id == agent_id]

    async def seed_tool(self, record: ToolRecord) -> ToolRecord:
        if record.name not in self._tools:
            self._tools[record.name] = record
        return self._tools[record.name]

    async def list_tools(self) -> list[ToolRecord]:
        return list(self._tools.values())

    async def set_workspace_tool(
        self, workspace_id: str, tool_name: str, enabled: bool
    ) -> WorkspaceToolRecord:
        self._workspace_tools = [
            t for t in self._workspace_tools if t.tool_name != tool_name
        ]
        record = WorkspaceToolRecord(
            workspace_id=workspace_id, tool_name=tool_name, enabled=enabled
        )
        self._workspace_tools.append(record)
        return record

    async def list_workspace_tools(
        self, workspace_id: str
    ) -> list[WorkspaceToolRecord]:
        return [t for t in self._workspace_tools if t.workspace_id == workspace_id]

    async def get_workspace_tool(
        self, workspace_id: str, tool_name: str
    ) -> WorkspaceToolRecord | None:
        for t in self._workspace_tools:
            if t.workspace_id == workspace_id and t.tool_name == tool_name:
                return t
        return None


# ── Postgres ─────────────────────────────────────────────────────────────────


class PostgresAgentDefinitionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_agent(self, record: AgentRecord) -> AgentRecord:
        async with self._session_factory() as session:
            row = AgentTable(
                id=record.id,
                workspace_id=record.workspace_id,
                name=record.name,
                model=record.model,
                prompt_ref=record.prompt_ref,
                temperature=record.temperature,
                max_steps=record.max_steps,
                enabled=record.enabled,
                created_by=record.created_by,
            )
            session.add(row)
            await session.commit()
            return _agent_row_to_record(row)

    async def find_agent_by_id(self, agent_id: str) -> AgentRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AgentTable).where(AgentTable.id == agent_id)
            )
            return _agent_row_to_record(row) if row else None

    async def list_agents(self, workspace_id: str) -> list[AgentRecord]:
        async with self._session_factory() as session:
            stmt = select(AgentTable).where(AgentTable.workspace_id == workspace_id)
            result = await session.scalars(stmt)
            return [_agent_row_to_record(row) for row in result]

    async def update_agent(self, record: AgentRecord) -> AgentRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AgentTable).where(AgentTable.id == record.id)
            )
            if row is None:
                return None
            row.name = record.name
            row.model = record.model
            row.prompt_ref = record.prompt_ref
            row.temperature = record.temperature
            row.max_steps = record.max_steps
            row.enabled = record.enabled
            await session.commit()
            return _agent_row_to_record(row)

    async def delete_agent(self, agent_id: str) -> bool:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AgentTable).where(AgentTable.id == agent_id)
            )
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def set_agent_tools(
        self, agent_id: str, tool_names: list[str]
    ) -> list[AgentToolRecord]:
        async with self._session_factory() as session:
            await session.execute(
                delete(AgentToolTable).where(AgentToolTable.agent_id == agent_id)
            )
            records: list[AgentToolRecord] = []
            for name in tool_names:
                at_row = AgentToolTable(agent_id=agent_id, tool_name=name)
                session.add(at_row)
                records.append(AgentToolRecord(id=0, agent_id=agent_id, tool_name=name))
            await session.commit()
            return records

    async def get_agent_tools(self, agent_id: str) -> list[AgentToolRecord]:
        async with self._session_factory() as session:
            stmt = select(AgentToolTable).where(AgentToolTable.agent_id == agent_id)
            result = await session.scalars(stmt)
            return [
                AgentToolRecord(
                    id=row.id, agent_id=row.agent_id, tool_name=row.tool_name
                )
                for row in result
            ]

    async def seed_tool(self, record: ToolRecord) -> ToolRecord:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(ToolTable).where(ToolTable.name == record.name)
            )
            if existing is not None:
                return _tool_row_to_record(existing)
            row = ToolTable(
                name=record.name,
                description=record.description,
                parameters_schema=record.parameters_schema,
                enabled_by_default=record.enabled_by_default,
                owner=record.owner,
            )
            session.add(row)
            await session.commit()
            return _tool_row_to_record(row)

    async def list_tools(self) -> list[ToolRecord]:
        async with self._session_factory() as session:
            stmt = select(ToolTable)
            result = await session.scalars(stmt)
            return [_tool_row_to_record(row) for row in result]

    async def set_workspace_tool(
        self, workspace_id: str, tool_name: str, enabled: bool
    ) -> WorkspaceToolRecord:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(WorkspaceToolTable).where(
                    WorkspaceToolTable.workspace_id == workspace_id,
                    WorkspaceToolTable.tool_name == tool_name,
                )
            )
            if row is None:
                row = WorkspaceToolTable(
                    workspace_id=workspace_id,
                    tool_name=tool_name,
                    enabled=enabled,
                )
                session.add(row)
            else:
                row.enabled = enabled
            await session.commit()
            return WorkspaceToolRecord(
                workspace_id=workspace_id,
                tool_name=tool_name,
                enabled=row.enabled,
            )

    async def list_workspace_tools(
        self, workspace_id: str
    ) -> list[WorkspaceToolRecord]:
        async with self._session_factory() as session:
            stmt = select(WorkspaceToolTable).where(
                WorkspaceToolTable.workspace_id == workspace_id
            )
            result = await session.scalars(stmt)
            return [
                WorkspaceToolRecord(
                    workspace_id=row.workspace_id,
                    tool_name=row.tool_name,
                    enabled=row.enabled,
                )
                for row in result
            ]

    async def get_workspace_tool(
        self, workspace_id: str, tool_name: str
    ) -> WorkspaceToolRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(WorkspaceToolTable).where(
                    WorkspaceToolTable.workspace_id == workspace_id,
                    WorkspaceToolTable.tool_name == tool_name,
                )
            )
            if row is None:
                return None
            return WorkspaceToolRecord(
                workspace_id=row.workspace_id,
                tool_name=row.tool_name,
                enabled=row.enabled,
            )


def _agent_row_to_record(row: AgentTable) -> AgentRecord:
    return AgentRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        model=row.model,
        prompt_ref=row.prompt_ref,
        temperature=row.temperature,
        max_steps=row.max_steps,
        enabled=row.enabled,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _tool_row_to_record(row: ToolTable) -> ToolRecord:
    return ToolRecord(
        name=row.name,
        description=row.description,
        parameters_schema=(
            row.parameters_schema if isinstance(row.parameters_schema, dict) else {}
        ),
        enabled_by_default=row.enabled_by_default,
        owner=row.owner,
        created_at=row.created_at,
    )
