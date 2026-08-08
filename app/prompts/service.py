"""Prompt Registry service — render, create version, activate, rollback."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from app.prompts.models import PromptRecord, PromptVersionSummary
from app.prompts.repository import PromptRepository

logger = logging.getLogger(__name__)

# Prompt references may pin a version: "name@3" renders version 3 of
# "name" regardless of the currently active version; plain names always
# render the active version.
_PROMPT_REF_PIN_RE = re.compile(r"^(.+)@(\d+)$")


def split_prompt_ref(prompt_ref: str) -> tuple[str, int | None]:
    """Split a prompt reference into (name, pinned version or None)."""
    match = _PROMPT_REF_PIN_RE.match(prompt_ref)
    if match:
        return match.group(1), int(match.group(2))
    return prompt_ref, None


class PromptRegistryService:
    """Centralized prompt template registry.

    Fallback behaviour: when no template is found for the requested name,
    the caller-supplied *fallback* string is returned.  This ensures the
    platform remains functional even when the registry is empty (e.g.
    first startup before seeds run or in-memory mode).
    """

    def __init__(self, repository: PromptRepository) -> None:
        self._repo = repository

    async def seed(
        self,
        name: str,
        content: str,
        *,
        variables: list[dict[str, object]] | None = None,
        workspace_id: str | None = None,
    ) -> PromptRecord:
        """Idempotently seed a built-in template (workspace_id=NULL → global)."""
        record = PromptRecord(
            workspace_id=workspace_id,
            name=name,
            version=1,
            content=content,
            variables=variables or [],
            is_active=True,
        )
        return await self._repo.seed(record)

    async def render(
        self,
        name: str,
        variables: dict[str, str] | None = None,
        fallback: str = "",
        *,
        workspace_id: str | None = None,
    ) -> str:
        """Render the active version of a template with variable substitution.

        Resolves workspace-scoped template first, then falls back to global
        (workspace_id=NULL).  If neither exists, returns *fallback*.
        """
        template = await self._repo.find_active(workspace_id, name)
        if template is None and workspace_id is not None:
            template = await self._repo.find_active(None, name)
        if template is None:
            return fallback

        content = template.content
        if variables:
            for key, val in variables.items():
                placeholder = "{" + key + "}"
                content = content.replace(placeholder, val)
        return content

    async def create_version(
        self,
        name: str,
        content: str,
        *,
        variables: list[dict[str, object]] | None = None,
        workspace_id: str | None = None,
        created_by: str | None = None,
    ) -> PromptRecord:
        """Create a new version of a template (auto-incremented)."""
        versions = await self._repo.list_versions(workspace_id, name)
        next_version = 1
        if versions:
            next_version = max(v.version for v in versions) + 1

        record = PromptRecord(
            workspace_id=workspace_id,
            name=name,
            version=next_version,
            content=content,
            variables=variables or [],
            is_active=False,
            created_by=created_by,
            created_at=datetime.now(UTC),
        )
        return await self._repo.create_version(record)

    async def activate(
        self,
        name: str,
        version: int,
        *,
        workspace_id: str | None = None,
    ) -> bool:
        """Set a specific version as active (also serves as rollback)."""
        return await self._repo.set_active(workspace_id, name, version)

    async def resolve_version(
        self, name: str, *, workspace_id: str | None = None
    ) -> int | None:
        """Return the active version number of a template.

        Workspace-scoped template wins, global is the fallback; None when
        no template exists.  Used by the run audit to record the exact
        prompt version an agent executed with.
        """
        template = await self._repo.find_active(workspace_id, name)
        if template is None and workspace_id is not None:
            template = await self._repo.find_active(None, name)
        return template.version if template is not None else None

    async def render_version(
        self,
        name: str,
        version: int,
        variables: dict[str, str] | None = None,
        fallback: str = "",
        *,
        workspace_id: str | None = None,
    ) -> str:
        """Render a pinned version ("name@version") of a template.

        Same resolution order as ``render`` (workspace first, then global)
        but for an exact version; returns *fallback* when absent.
        """
        template = await self._repo.find_by_name_and_version(
            workspace_id, name, version
        )
        if template is None and workspace_id is not None:
            template = await self._repo.find_by_name_and_version(None, name, version)
        if template is None:
            return fallback
        content = template.content
        if variables:
            for key, val in variables.items():
                placeholder = "{" + key + "}"
                content = content.replace(placeholder, val)
        return content

    async def list_versions(
        self, name: str, *, workspace_id: str | None = None
    ) -> list[PromptVersionSummary]:
        records = await self._repo.list_versions(workspace_id, name)
        return [
            PromptVersionSummary(
                name=r.name,
                version=r.version,
                is_active=r.is_active,
                created_at=r.created_at,
            )
            for r in records
        ]

    async def list_active_templates(
        self, *, workspace_id: str | None = None
    ) -> list[PromptRecord]:
        return await self._repo.list_active_templates(workspace_id)
