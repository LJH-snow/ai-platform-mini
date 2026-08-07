"""Workspaces API: CRUD + member management."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.auth import (
    provide_user_service,
    provide_workspace_service,
)
from app.auth.dependencies import require_api_key
from app.auth.identity import IdentityContext
from app.auth.models import APIKey
from app.auth.user_service import UserService
from app.auth.workspace_service import WorkspaceService
from app.auth.workspaces_repository import WorkspaceMemberRecord
from app.core.context import RequestContext
from app.exceptions.base import AuthenticationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


# ── Request / Response schemas ──────────────────────────────────────────────


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    role: str
    member_count: int | None = None


class WorkspaceListResponse(BaseModel):
    id: str
    name: str
    role: str
    member_count: int


class AddMemberRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=255)
    role: str = Field(..., min_length=1, max_length=16)


class UpdateMemberRoleRequest(BaseModel):
    role: str = Field(..., min_length=1, max_length=16)


class MemberResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str
    created_at: str | None = None


# ── Helpers ─────────────────────────────────────────────────────────────────


def _require_identity(request: Request) -> IdentityContext:
    context: RequestContext = request.state.context
    identity = context.identity
    if identity is None or identity.user_id is None:
        raise AuthenticationError(
            "Not authenticated as a user. Use a user-bound API key."
        )
    return identity


async def _resolve_member_email(
    user_service: UserService,
    member: WorkspaceMemberRecord,
) -> tuple[str, str]:
    """Resolve display_name and email for a member record."""
    user = await user_service.get_user(member.user_id)
    if user is None:
        return member.user_id[:8], "unknown@deleted"
    return user.display_name, user.email


# ── Routes ──────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=201,
    summary="Create a new workspace",
)
async def create_workspace(
    body: CreateWorkspaceRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    ws_service: Annotated[WorkspaceService, Depends(provide_workspace_service)],
) -> WorkspaceResponse:
    identity = _require_identity(request)
    if identity.user_id is None:  # type: ignore[unreachable]
        raise AuthenticationError("Not authenticated as a user.")

    ws, role = await ws_service.create_workspace(
        user_id=identity.user_id,
        name=body.name,
    )
    return WorkspaceResponse(id=ws.id, name=ws.name, role=role)


@router.get(
    "",
    response_model=list[WorkspaceListResponse],
    summary="List workspaces for the current user",
)
async def list_workspaces(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    ws_service: Annotated[WorkspaceService, Depends(provide_workspace_service)],
) -> list[WorkspaceListResponse]:
    identity = _require_identity(request)
    if identity.user_id is None:  # type: ignore[unreachable]
        raise AuthenticationError("Not authenticated as a user.")

    user_ws = await ws_service.list_workspaces_for_user(identity.user_id)
    result: list[WorkspaceListResponse] = []
    for ws, role in user_ws:
        members = await ws_service.list_members(ws.id, identity.user_id)
        result.append(
            WorkspaceListResponse(
                id=ws.id,
                name=ws.name,
                role=role,
                member_count=len(members),
            )
        )
    return result


@router.post(
    "/{workspace_id}/members",
    response_model=MemberResponse,
    status_code=201,
    summary="Add a member to a workspace",
)
async def add_member(
    workspace_id: str,
    body: AddMemberRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    ws_service: Annotated[WorkspaceService, Depends(provide_workspace_service)],
    user_service: Annotated[UserService, Depends(provide_user_service)],
) -> MemberResponse:
    identity = _require_identity(request)
    if identity.user_id is None:  # type: ignore[unreachable]
        raise AuthenticationError("Not authenticated as a user.")

    member = await ws_service.add_member(
        workspace_id=workspace_id,
        actor_user_id=identity.user_id,
        target_email=body.email,
        role=body.role,
    )

    display_name, email = await _resolve_member_email(user_service, member)
    return MemberResponse(
        user_id=member.user_id,
        email=email,
        display_name=display_name,
        role=member.role,
        created_at=member.created_at.isoformat() if member.created_at else None,
    )


@router.get(
    "/{workspace_id}/members",
    response_model=list[MemberResponse],
    summary="List all members of a workspace",
)
async def list_members(
    workspace_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    ws_service: Annotated[WorkspaceService, Depends(provide_workspace_service)],
    user_service: Annotated[UserService, Depends(provide_user_service)],
) -> list[MemberResponse]:
    identity = _require_identity(request)
    if identity.user_id is None:  # type: ignore[unreachable]
        raise AuthenticationError("Not authenticated as a user.")

    members = await ws_service.list_members(workspace_id, identity.user_id)
    result: list[MemberResponse] = []
    for m in members:
        display_name, email = await _resolve_member_email(user_service, m)
        result.append(
            MemberResponse(
                user_id=m.user_id,
                email=email,
                display_name=display_name,
                role=m.role,
                created_at=m.created_at.isoformat() if m.created_at else None,
            )
        )
    return result


@router.put(
    "/{workspace_id}/members/{user_id}",
    status_code=200,
    summary="Update a member's role",
)
async def update_member_role(
    workspace_id: str,
    user_id: str,
    body: UpdateMemberRoleRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    ws_service: Annotated[WorkspaceService, Depends(provide_workspace_service)],
) -> dict[str, str]:
    identity = _require_identity(request)
    if identity.user_id is None:  # type: ignore[unreachable]
        raise AuthenticationError("Not authenticated as a user.")

    await ws_service.update_member_role(
        workspace_id=workspace_id,
        actor_user_id=identity.user_id,
        target_user_id=user_id,
        role=body.role,
    )
    return {"status": "ok", "role": body.role}


@router.delete(
    "/{workspace_id}/members/{user_id}",
    status_code=200,
    summary="Remove a member from a workspace",
)
async def remove_member(
    workspace_id: str,
    user_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    ws_service: Annotated[WorkspaceService, Depends(provide_workspace_service)],
) -> dict[str, str]:
    identity = _require_identity(request)
    if identity.user_id is None:  # type: ignore[unreachable]
        raise AuthenticationError("Not authenticated as a user.")

    await ws_service.remove_member(
        workspace_id=workspace_id,
        actor_user_id=identity.user_id,
        target_user_id=user_id,
    )
    return {"status": "ok"}
