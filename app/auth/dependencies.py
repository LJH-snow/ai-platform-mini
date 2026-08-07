import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.hash import hash_api_key
from app.auth.identity import IdentityContext
from app.auth.memory_repository import create_in_memory_repository
from app.auth.models import APIKey
from app.auth.repository import APIKeyRepository
from app.auth.service import APIKeyService
from app.core.context import RequestContext
from app.core.settings import get_settings
from app.exceptions.base import AuthenticationError, AuthorizationError

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache
def provide_api_key_service() -> APIKeyService:
    settings = get_settings()
    if settings.auth_storage == "postgres":
        from app.auth.postgres_repository import PostgresAPIKeyRepository
        from app.db.session import create_async_session_factory

        session_factory = create_async_session_factory()
        repository: APIKeyRepository = PostgresAPIKeyRepository(session_factory)
    else:
        repository = create_in_memory_repository(settings.api_keys.get_secret_value())
    return APIKeyService(repository=repository)


@lru_cache
def _admin_key_hashes() -> frozenset[str]:
    settings = get_settings()
    raw = settings.admin_api_keys.get_secret_value()
    if not raw:
        return frozenset()
    return frozenset(hash_api_key(k.strip()) for k in raw.split(",") if k.strip())


def is_configured_admin_key_hash(key_hash: str) -> bool:
    """Return whether a key hash belongs to the configured admin allowlist."""

    return key_hash in _admin_key_hashes()


def is_configured_admin_key_prefix(prefix: str) -> bool:
    """Return whether a hash prefix belongs to a configured admin key."""

    return any(key_hash.startswith(prefix) for key_hash in _admin_key_hashes())


async def require_admin_key(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(_bearer_scheme)
    ],
    service: Annotated[APIKeyService, Depends(provide_api_key_service)],
) -> APIKey:
    if credentials is None:
        raise AuthenticationError("Missing Authorization header.")

    api_key = await service.validate(credentials.credentials)
    admin_hashes = _admin_key_hashes()

    if not admin_hashes:
        raise AuthorizationError("Admin access not configured.")

    if api_key.key not in admin_hashes:
        raise AuthorizationError("Admin access required.")

    await _update_context(request, api_key)
    return api_key


async def require_api_key(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(_bearer_scheme)
    ],
    service: Annotated[APIKeyService, Depends(provide_api_key_service)],
) -> APIKey:
    settings = get_settings()

    if not settings.auth_enabled:
        await _update_context(
            request,
            APIKey(key="disabled", name="authentication-disabled"),
        )
        return APIKey(key="disabled", name="authentication-disabled")

    if credentials is None:
        raise AuthenticationError("Missing Authorization header.")

    api_key = await service.validate(credentials.credentials)
    await _update_context(request, api_key)
    return api_key


async def _update_context(request: Request, api_key: APIKey) -> None:
    context: RequestContext = getattr(request.state, "context", None)  # type: ignore[assignment]
    if context is None:
        return

    # Resolve workspace role when both user and workspace are bound
    role: str | None = None
    if api_key.workspace_id is not None and api_key.user_id is not None:
        role = await _resolve_role(api_key.workspace_id, api_key.user_id)

    identity = IdentityContext(
        user_id=api_key.user_id,
        workspace_id=api_key.workspace_id,
        api_key_id=api_key.id,
        api_key_hash=api_key.key,
        role=role,
    )
    request.state.context = context.with_auth(api_key.key, api_key.name).with_identity(
        identity
    )


async def _resolve_role(workspace_id: str, user_id: str) -> str | None:
    """Resolve the workspace role for a user.

    Uses the same storage backend as the auth system to avoid introducing
    a hard dependency on Postgres at the dependency level.
    """
    settings = get_settings()
    try:
        if settings.auth_storage == "postgres":
            from app.auth.workspaces_repository import PostgresWorkspaceRepository
            from app.db.session import create_async_session_factory

            session_factory = create_async_session_factory()
            ws_repo: PostgresWorkspaceRepository = PostgresWorkspaceRepository(
                session_factory
            )
        else:
            # Memory mode: role isn't persisted across requests, so we
            # can only return None here.  register/login set identity
            # directly with the correct role.
            return None

        member = await ws_repo.get_member(workspace_id, user_id)
        return member.role if member else None
    except Exception:
        logger.debug(
            "Failed to resolve role for user=%s workspace=%s",
            user_id,
            workspace_id,
            exc_info=True,
        )
        return None
