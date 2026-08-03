import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.hash import hash_api_key
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
        repository = create_in_memory_repository(settings.api_keys)
    return APIKeyService(repository=repository)


@lru_cache
def _admin_key_hashes() -> frozenset[str]:
    settings = get_settings()
    raw = settings.admin_api_keys
    if not raw:
        return frozenset()
    return frozenset(hash_api_key(k.strip()) for k in raw.split(",") if k.strip())


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

    _update_context(request, api_key.key, api_key.name)
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
        _update_context(request, "disabled", "authentication-disabled")
        return APIKey(key="disabled", name="authentication-disabled")

    if credentials is None:
        raise AuthenticationError("Missing Authorization header.")

    api_key = await service.validate(credentials.credentials)
    _update_context(request, api_key.key, api_key.name)
    return api_key


def _update_context(request: Request, key: str, name: str) -> None:
    context: RequestContext = getattr(request.state, "context", None)  # type: ignore[assignment]
    if context is not None:
        request.state.context = context.with_auth(key, name)
