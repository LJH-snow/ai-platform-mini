"""Workspace service: CRUD + member management with role-based access control."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from app.auth.users_repository import UserRepository
from app.auth.workspaces_repository import (
    WorkspaceMemberRecord,
    WorkspaceRecord,
    WorkspaceRepository,
)
from app.exceptions.base import (
    AuthorizationError,
    ValidationError,
)

logger = logging.getLogger(__name__)

# Allowed roles and hierarchy
_ROLES = frozenset({"owner", "admin", "member", "viewer"})
# Roles that can manage members (add / remove / change roles)
_MEMBER_MANAGEMENT_ROLES = frozenset({"owner", "admin"})
# Roles that can create resources (agents, knowledge bases, etc.)
_RESOURCE_CREATION_ROLES = frozenset({"owner", "admin", "member"})


class WorkspaceService:
    def __init__(
        self,
        workspace_repo: WorkspaceRepository,
        user_repo: UserRepository,
    ) -> None:
        self._ws_repo = workspace_repo
        self._user_repo = user_repo

    async def create_workspace(
        self, user_id: str, name: str
    ) -> tuple[WorkspaceRecord, str]:
        """Create a workspace and add the creator as owner."""
        name = name.strip()
        if not name:
            raise ValidationError("Workspace name is required.")

        ws_id = str(uuid.uuid4())
        ws_record = WorkspaceRecord(
            id=ws_id,
            name=name,
            created_by_user_id=user_id,
            created_at=datetime.now(UTC),
        )
        saved = await self._ws_repo.create_workspace(ws_record)
        await self._ws_repo.add_member(ws_id, user_id, "owner")
        logger.info("workspace_created id=%s name=%s owner=%s", ws_id, name, user_id)
        return saved, "owner"

    async def get_workspace(self, workspace_id: str) -> WorkspaceRecord | None:
        return await self._ws_repo.find_workspace_by_id(workspace_id)

    async def list_workspaces_for_user(
        self,
        user_id: str,
    ) -> list[tuple[WorkspaceRecord, str]]:
        return await self._ws_repo.list_workspaces_for_user(user_id)

    # ── Member management ───────────────────────────────────────────────

    async def _require_member_role(
        self,
        workspace_id: str,
        user_id: str,
        allowed_roles: frozenset[str],
    ) -> WorkspaceMemberRecord:
        member = await self._ws_repo.get_member(workspace_id, user_id)
        if member is None:
            raise AuthorizationError("You are not a member of this workspace.")
        if member.role not in allowed_roles:
            raise AuthorizationError(
                f"Role '{member.role}' is not allowed for this operation."
            )
        return member

    async def add_member(
        self,
        workspace_id: str,
        actor_user_id: str,
        target_email: str,
        role: str,
    ) -> WorkspaceMemberRecord:
        """Add a user to a workspace.  Only owner/admin can manage members."""
        await self._require_member_role(
            workspace_id, actor_user_id, _MEMBER_MANAGEMENT_ROLES
        )

        role = role.lower()
        if role not in _ROLES:
            raise ValidationError(
                f"Invalid role '{role}'. Must be one of: {', '.join(sorted(_ROLES))}."
            )
        # owner role is only granted at workspace creation; prevent promotion
        if role == "owner":
            raise ValidationError(
                "Cannot assign the 'owner' role via member management."
            )

        target_user = await self._user_repo.find_by_email(target_email.strip().lower())
        if target_user is None:
            raise ValidationError(f"User with email '{target_email}' not found.")

        return await self._ws_repo.add_member(workspace_id, target_user.id, role)

    async def list_members(
        self,
        workspace_id: str,
        actor_user_id: str,
    ) -> list[WorkspaceMemberRecord]:
        """List all members. All workspace members can view the member list."""
        await self._require_member_role(workspace_id, actor_user_id, _ROLES)
        return await self._ws_repo.list_members(workspace_id)

    async def update_member_role(
        self,
        workspace_id: str,
        actor_user_id: str,
        target_user_id: str,
        role: str,
    ) -> None:
        """Change a member's role. Only owner/admin."""
        await self._require_member_role(
            workspace_id, actor_user_id, _MEMBER_MANAGEMENT_ROLES
        )

        role = role.lower()
        if role not in _ROLES:
            raise ValidationError(
                f"Invalid role '{role}'. Must be one of: {', '.join(sorted(_ROLES))}."
            )
        if role == "owner":
            raise ValidationError("Cannot assign the 'owner' role via role update.")

        target = await self._ws_repo.get_member(workspace_id, target_user_id)
        if target is None:
            raise ValidationError("Target user is not a member of this workspace.")
        if target.role == "owner":
            raise AuthorizationError("Cannot change the role of the workspace owner.")

        updated = await self._ws_repo.update_member_role(
            workspace_id, target_user_id, role
        )
        if not updated:
            raise ValidationError("Failed to update member role.")

    async def remove_member(
        self,
        workspace_id: str,
        actor_user_id: str,
        target_user_id: str,
    ) -> None:
        """Remove a member. Only owner/admin. Cannot remove the owner."""
        await self._require_member_role(
            workspace_id, actor_user_id, _MEMBER_MANAGEMENT_ROLES
        )

        target = await self._ws_repo.get_member(workspace_id, target_user_id)
        if target is None:
            raise ValidationError("Target user is not a member of this workspace.")
        if target.role == "owner":
            raise AuthorizationError("Cannot remove the workspace owner.")

        removed = await self._ws_repo.remove_member(workspace_id, target_user_id)
        if not removed:
            raise ValidationError("Failed to remove member.")
