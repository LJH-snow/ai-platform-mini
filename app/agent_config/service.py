"""Agent Definition service — CRUD + tool whitelist validation."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.agent_config.models import AgentRecord, ToolRecord, WorkspaceToolRecord
from app.agent_config.repository import AgentDefinitionRepository
from app.audit.service import AuditActor, AuditService
from app.exceptions.base import ValidationError
from app.prompts.service import PromptRegistryService, split_prompt_ref

if TYPE_CHECKING:
    from app.billing.entitlement import EntitlementService
    from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class AgentDefinitionService:
    def __init__(
        self,
        repository: AgentDefinitionRepository,
        tool_registry: ToolRegistry,
        prompt_registry: PromptRegistryService | None = None,
        audit: AuditService | None = None,
        # E1a: agent ceiling checkpoint.  None disables the check
        # (pre-E1 behaviour) — legacy wiring stays untouched.
        entitlement: EntitlementService | None = None,
    ) -> None:
        self._repo = repository
        self._audit = audit
        self._tool_registry = tool_registry
        self._prompt_registry = prompt_registry
        self._entitlement = entitlement

    # ── Agents ────────────────────────────────────────────────────────────

    @staticmethod
    def _agent_snapshot(record: AgentRecord) -> dict[str, object]:
        """Key fields for the audit before/after diff (not the full object)."""
        return {
            "name": record.name,
            "model": record.model,
            "prompt_ref": record.prompt_ref,
            "temperature": record.temperature,
            "max_steps": record.max_steps,
            "enabled": record.enabled,
        }

    async def create_agent(
        self,
        workspace_id: str,
        name: str,
        model: str,
        prompt_ref: str,
        *,
        tool_names: list[str] | None = None,
        temperature: float = 0.7,
        max_steps: int = 10,
        created_by: str | None = None,
        actor: AuditActor | None = None,
    ) -> tuple[AgentRecord, list[str]]:
        """Create an agent and optionally bind tools."""
        name = name.strip()
        if not name:
            raise ValidationError("Agent name is required.")
        if not model.strip():
            raise ValidationError("Model is required.")

        agent_id = str(uuid.uuid4())
        prompt_ref = prompt_ref.strip()
        await self._validate_prompt_ref(prompt_ref, workspace_id=workspace_id)
        # E1a checkpoint (the only agent resource ceiling): refuse new
        # agents beyond the plan's max_agents.  Legacy (no subscription)
        # passes through untouched.
        if self._entitlement is not None:
            existing = await self._repo.list_agents(workspace_id)
            await self._entitlement.require_limit(workspace_id, "agent", len(existing))
        record = AgentRecord(
            id=agent_id,
            workspace_id=workspace_id,
            name=name,
            model=model,
            prompt_ref=prompt_ref,
            temperature=temperature,
            max_steps=max_steps,
            enabled=True,
            created_by=created_by,
            created_at=datetime.now(UTC),
        )
        saved = await self._repo.create_agent(record)

        bound_tools: list[str] = []
        if tool_names:
            bound_tools = await self._validate_tool_names(
                tool_names, workspace_id=workspace_id
            )
            await self._repo.set_agent_tools(agent_id, bound_tools)

        logger.info("agent_created id=%s name=%s", agent_id, name)
        if self._audit is not None and actor is not None:
            await self._audit.record(
                action="agent.create",
                resource_type="agent",
                resource_id=agent_id,
                actor=actor,
                after=self._agent_snapshot(saved),
            )
        return saved, bound_tools

    async def get_agent(
        self, agent_id: str, *, workspace_id: str | None = None
    ) -> AgentRecord | None:
        record = await self._repo.find_agent_by_id(agent_id)
        if record is None:
            return None
        if workspace_id is not None and record.workspace_id != workspace_id:
            return None
        return record

    async def list_agents(self, workspace_id: str) -> list[AgentRecord]:
        return await self._repo.list_agents(workspace_id)

    async def update_agent(
        self,
        agent_id: str,
        *,
        workspace_id: str | None = None,
        name: str | None = None,
        model: str | None = None,
        prompt_ref: str | None = None,
        tool_names: list[str] | None = None,
        temperature: float | None = None,
        max_steps: int | None = None,
        enabled: bool | None = None,
        actor: AuditActor | None = None,
    ) -> AgentRecord | None:
        existing = await self._repo.find_agent_by_id(agent_id)
        if existing is None:
            return None
        if workspace_id is not None and existing.workspace_id != workspace_id:
            return None

        if name is not None:
            existing.name = name.strip()
        if model is not None:
            existing.model = model.strip()
        if prompt_ref is not None:
            existing.prompt_ref = prompt_ref.strip()
            await self._validate_prompt_ref(
                existing.prompt_ref, workspace_id=workspace_id
            )
        before_snapshot = self._agent_snapshot(existing)
        if temperature is not None:
            existing.temperature = temperature
        if max_steps is not None:
            existing.max_steps = max_steps
        if enabled is not None:
            existing.enabled = enabled

        result = await self._repo.update_agent(existing)

        if tool_names is not None:
            bound = await self._validate_tool_names(
                tool_names, workspace_id=workspace_id
            )
            await self._repo.set_agent_tools(agent_id, bound)

        if self._audit is not None and actor is not None and result is not None:
            await self._audit.record(
                action="agent.update",
                resource_type="agent",
                resource_id=agent_id,
                actor=actor,
                before=before_snapshot,
                after=self._agent_snapshot(result),
            )

        return result

    async def delete_agent(
        self,
        agent_id: str,
        *,
        workspace_id: str | None = None,
        actor: AuditActor | None = None,
    ) -> bool:
        record = await self._repo.find_agent_by_id(agent_id)
        if record is None:
            return False
        if workspace_id is not None and record.workspace_id != workspace_id:
            return False
        deleted = await self._repo.delete_agent(agent_id)
        if deleted and self._audit is not None and actor is not None:
            await self._audit.record(
                action="agent.delete",
                resource_type="agent",
                resource_id=agent_id,
                actor=actor,
                before=self._agent_snapshot(record),
            )
        return deleted

    async def get_agent_tools(self, agent_id: str) -> list[str]:
        tools = await self._repo.get_agent_tools(agent_id)
        return [t.tool_name for t in tools]

    # ── Tool validation ────────────────────────────────────────────────────

    async def _validate_tool_names(
        self, names: list[str], *, workspace_id: str | None = None
    ) -> list[str]:
        valid: list[str] = []
        for name in names:
            if self._tool_registry.get(name) is None:
                raise ValidationError(
                    f"Tool '{name}' is not available in the tool registry."
                )
            if workspace_id is not None and not await self.is_tool_enabled(
                workspace_id, name
            ):
                raise ValidationError(f"Tool '{name}' is disabled in this workspace.")
            valid.append(name)
        return valid

    async def _validate_prompt_ref(
        self, prompt_ref: str, *, workspace_id: str | None
    ) -> None:
        """Reject agent definitions that reference a missing prompt template.

        Empty references are allowed (the runtime falls back to the built-in
        decision protocol).  Non-empty references must resolve — either to
        the active template (plain name) or to an existing pinned version
        ("name@version") — otherwise the agent would silently degrade to
        the default protocol prompt at run time.
        """

        if not prompt_ref or self._prompt_registry is None:
            return
        name, pinned = split_prompt_ref(prompt_ref)
        if pinned is None:
            rendered = await self._prompt_registry.render(
                prompt_ref, fallback="", workspace_id=workspace_id
            )
        else:
            rendered = await self._prompt_registry.render_version(
                name, pinned, fallback="", workspace_id=workspace_id
            )
        if not rendered:
            raise ValidationError(
                f"Prompt template '{prompt_ref}' not found in the registry."
            )

    # ── Tool seeds ─────────────────────────────────────────────────────────

    async def seed_tool(
        self,
        name: str,
        description: str,
        parameters_schema: dict[str, object],
        *,
        enabled_by_default: bool = False,
        owner: str = "builtin",
    ) -> ToolRecord:
        return await self._repo.seed_tool(
            ToolRecord(
                name=name,
                description=description,
                parameters_schema=parameters_schema,
                enabled_by_default=enabled_by_default,
                owner=owner,
            )
        )

    async def list_tools(self) -> list[ToolRecord]:
        return await self._repo.list_tools()

    # ── Workspace tool enablement ─────────────────────────────────────────

    async def set_tool_enabled(
        self,
        workspace_id: str,
        tool_name: str,
        enabled: bool,
        *,
        actor: AuditActor | None = None,
    ) -> WorkspaceToolRecord:
        """Override a tool's enablement for one workspace."""
        if self._tool_registry.get(tool_name) is None:
            raise ValidationError(
                f"Tool '{tool_name}' is not available in the tool registry."
            )
        previous = await self.is_tool_enabled(workspace_id, tool_name)
        override = await self._repo.set_workspace_tool(workspace_id, tool_name, enabled)
        if self._audit is not None and actor is not None:
            await self._audit.record(
                action="tool.enable",
                resource_type="tool",
                resource_id=tool_name,
                actor=actor,
                before={"tool_name": tool_name, "enabled": previous},
                after={"tool_name": tool_name, "enabled": enabled},
            )
        return override

    async def is_tool_enabled(self, workspace_id: str, tool_name: str) -> bool:
        """Effective enablement: workspace override wins, else the global default."""
        override = await self._repo.get_workspace_tool(workspace_id, tool_name)
        if override is not None:
            return override.enabled
        if self._tool_registry.get(tool_name) is None:
            return False
        for record in await self._repo.list_tools():
            if record.name == tool_name:
                return record.enabled_by_default
        # Registry-only tool without a seeded record: default to enabled.
        return True

    async def list_tools_with_state(
        self, workspace_id: str | None
    ) -> list[dict[str, object]]:
        """Registry tools enriched with the workspace-effective enabled flag.

        ``workspace_id`` None returns the global view: no workspace
        overrides, enabled follows the seeded default.  Batches the
        workspace overrides and the seeded records into in-memory maps so
        the per-tool effective state is O(1) lookups instead of per-tool
        repository round trips.
        """
        records = await self._repo.list_tools()
        overrides: dict[str, bool] = {}
        if workspace_id is not None:
            overrides = {
                override.tool_name: override.enabled
                for override in await self._repo.list_workspace_tools(workspace_id)
            }
        if not records:
            # Seed failed or the DB is empty: fall back to the in-code
            # runtime registry so the tool surface is never empty.  The
            # built-in registry IS the seed source (roadmap B2); a later
            # successful bootstrap persists it.
            return [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters_schema": dict(tool.input_schema),
                    "owner": "builtin",
                    "enabled_by_default": True,
                    "enabled": True,
                }
                for tool in self._tool_registry.list_tools()
            ]
        defaults = {record.name: record.enabled_by_default for record in records}
        result: list[dict[str, object]] = []
        for record in records:
            enabled = overrides.get(record.name)
            if enabled is None:
                enabled = defaults.get(record.name, True)
            result.append(
                {
                    "name": record.name,
                    "description": record.description,
                    "parameters_schema": record.parameters_schema,
                    "owner": record.owner,
                    "enabled_by_default": record.enabled_by_default,
                    "enabled": enabled,
                }
            )
        return result
