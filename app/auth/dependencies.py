import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.models import APIKey
from app.auth.service import APIKeyService, create_api_key_service
from app.core.context import RequestContext
from app.core.settings import get_settings
from app.exceptions.base import AuthenticationError

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache
def provide_api_key_service() -> APIKeyService:
    return create_api_key_service()


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

    if not service.key_count:
        _update_context(request, "anonymous", "anonymous")
        return APIKey(key="anonymous", name="anonymous")

    if credentials is None:
        raise AuthenticationError("Missing Authorization header.")

    api_key = service.validate(credentials.credentials)
    _update_context(request, api_key.key, api_key.name)
    return api_key


def _update_context(request: Request, key: str, name: str) -> None:
    context: RequestContext = getattr(request.state, "context", None)  # type: ignore[assignment]
    if context is not None:
        request.state.context = context.with_auth(key, name)
