"""Auth API: register, login, me."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.dependencies import provide_api_key_service, require_api_key
from app.auth.identity import IdentityContext
from app.auth.models import APIKey
from app.auth.service import APIKeyService
from app.auth.user_service import UserService
from app.auth.users_repository import (
    InMemoryUserRepository,
    PostgresUserRepository,
    UserRecord,
    UserRepository,
)
from app.auth.workspace_service import WorkspaceService
from app.auth.workspaces_repository import (
    InMemoryWorkspaceRepository,
    PostgresWorkspaceRepository,
    WorkspaceRepository,
)
from app.core.context import RequestContext
from app.core.settings import get_settings
from app.exceptions.base import AuthenticationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ── Request / Response schemas ──────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=255)
    display_name: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    status: str


class WorkspaceSummary(BaseModel):
    id: str
    name: str
    role: str


class RegisterResponse(BaseModel):
    user: UserResponse
    workspace: WorkspaceSummary
    api_key: str


class LoginResponse(BaseModel):
    user: UserResponse
    workspaces: list[WorkspaceSummary]
    api_key: str


class MeResponse(BaseModel):
    user: UserResponse
    workspaces: list[WorkspaceSummary]


# ── Service providers ───────────────────────────────────────────────────────


@lru_cache
def _provide_user_repository() -> UserRepository:
    settings = get_settings()
    if settings.auth_storage == "postgres":
        from app.db.session import create_async_session_factory

        session_factory = create_async_session_factory()
        return PostgresUserRepository(session_factory)
    return InMemoryUserRepository()


@lru_cache
def _provide_workspace_repository() -> WorkspaceRepository:
    settings = get_settings()
    if settings.auth_storage == "postgres":
        from app.db.session import create_async_session_factory

        session_factory = create_async_session_factory()
        return PostgresWorkspaceRepository(session_factory)
    return InMemoryWorkspaceRepository()


@lru_cache
def provide_user_service() -> UserService:
    return UserService(repository=_provide_user_repository())


@lru_cache
def provide_workspace_service() -> WorkspaceService:
    return WorkspaceService(
        workspace_repo=_provide_workspace_repository(),
        user_repo=_provide_user_repository(),
    )


# Helper to clear auth caches in container shutdown
def _clear_auth_service_caches() -> None:
    provide_user_service.cache_clear()
    provide_workspace_service.cache_clear()
    _provide_user_repository.cache_clear()
    _provide_workspace_repository.cache_clear()


def _user_to_response(user: UserRecord) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
    )


# ── Routes ──────────────────────────────────────────────────────────────────


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=201,
    summary="Register a new user",
)
async def register(
    body: RegisterRequest,
    request: Request,
    user_service: Annotated[UserService, Depends(provide_user_service)],
    ws_service: Annotated[WorkspaceService, Depends(provide_workspace_service)],
    key_service: Annotated[APIKeyService, Depends(provide_api_key_service)],
) -> RegisterResponse:
    # TODO(Sprint A5): add IP-based rate limiting for public auth endpoints;
    # current require_rate_limit depends on API key which is unavailable here.
    # 1. Create user
    user = await user_service.register(
        email=body.email,
        display_name=body.display_name,
        password=body.password,
    )

    # 2. Create default workspace
    ws, role = await ws_service.create_workspace(
        user_id=user.id,
        name=f"{user.display_name}'s Workspace",
    )

    # 3. Issue a bound API key (A2 will also bind user_id/workspace_id)
    metadata, raw_key = await key_service.create_key(
        name=f"{user.email}-default",
        user_id=user.id,
        workspace_id=ws.id,
    )

    # 4. Set identity on request context
    from app.auth.hash import hash_api_key

    identity = IdentityContext(
        user_id=user.id,
        workspace_id=ws.id,
        api_key_id=metadata.id,
        api_key_hash=hash_api_key(raw_key),
        role=role,
    )
    context: RequestContext = request.state.context
    request.state.context = context.with_identity(identity)

    logger.info("user_registered_complete user_id=%s ws_id=%s", user.id, ws.id)

    return RegisterResponse(
        user=_user_to_response(user),
        workspace=WorkspaceSummary(id=ws.id, name=ws.name, role=role),
        api_key=raw_key,
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Login with email and password",
)
async def login(
    body: LoginRequest,
    request: Request,
    user_service: Annotated[UserService, Depends(provide_user_service)],
    ws_service: Annotated[WorkspaceService, Depends(provide_workspace_service)],
    key_service: Annotated[APIKeyService, Depends(provide_api_key_service)],
) -> LoginResponse:
    # TODO(Sprint A5): add IP-based rate limiting for public auth endpoints.
    settings = get_settings()
    if settings.auth_storage != "postgres":
        raise HTTPException(
            status_code=503,
            detail="Login requires auth_storage=postgres. "
            "Use memory mode with pre-configured API keys instead.",
        )

    # 1. Authenticate user
    user = await user_service.login(email=body.email, password=body.password)

    # 2. List workspaces
    user_ws = await ws_service.list_workspaces_for_user(user.id)
    workspaces = [
        WorkspaceSummary(id=ws.id, name=ws.name, role=role) for ws, role in user_ws
    ]

    if not workspaces:
        raise HTTPException(
            status_code=500,
            detail="User has no workspaces. This is a server configuration error.",
        )

    # 3. Issue a bound API key (use first workspace as default)
    primary_ws_id = workspaces[0].id
    metadata, raw_key = await key_service.create_key(
        name=f"{user.email}-login",
        user_id=user.id,
        workspace_id=primary_ws_id,
    )

    logger.info("user_logged_in user_id=%s", user.id)

    return LoginResponse(
        user=_user_to_response(user),
        workspaces=workspaces,
        api_key=raw_key,
    )


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Get current user profile and workspaces",
)
async def me(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    user_service: Annotated[UserService, Depends(provide_user_service)],
    ws_service: Annotated[WorkspaceService, Depends(provide_workspace_service)],
) -> MeResponse:
    context: RequestContext = request.state.context
    identity = context.identity

    if identity is None or identity.user_id is None:
        raise AuthenticationError(
            "Not authenticated as a user. Use a user-bound API key or login."
        )

    user = await user_service.get_user(identity.user_id)
    if user is None:
        raise AuthenticationError("Authenticated user no longer exists.")

    user_ws = await ws_service.list_workspaces_for_user(user.id)
    workspaces = [
        WorkspaceSummary(id=ws.id, name=ws.name, role=role) for ws, role in user_ws
    ]

    return MeResponse(
        user=_user_to_response(user),
        workspaces=workspaces,
    )
