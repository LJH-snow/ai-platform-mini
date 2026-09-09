import logging
from typing import Annotated

from fastapi import Depends, Request

from app.auth.dependencies import require_admin_key, require_api_key
from app.auth.models import APIKey
from app.core.container import (
    provide_auth_ip_rate_limit_service,
    provide_rate_limit_service,
)
from app.core.settings import get_settings
from app.ratelimit.service import RateLimitService

logger = logging.getLogger(__name__)


async def require_auth_ip_rate_limit(
    request: Request,
    service: Annotated[RateLimitService, Depends(provide_auth_ip_rate_limit_service)],
) -> None:
    settings = get_settings()

    if not settings.auth_ip_rate_limit_enabled:
        return

    client_ip = request.client.host if request.client is not None else "unknown"
    result = service.check_and_record_key(f"auth_ip:{client_ip}", client_ip)
    request.state.rate_limit_remaining = result.remaining
    request.state.rate_limit_limit = result.limit
    request.state.rate_limit_reset_after = result.reset_after


async def require_rate_limit(
    request: Request,
    api_key: Annotated[APIKey, Depends(require_api_key)],
    service: Annotated[RateLimitService, Depends(provide_rate_limit_service)],
) -> APIKey:
    settings = get_settings()

    if not settings.rate_limit_enabled:
        return api_key

    result = service.check_and_record(api_key)
    request.state.rate_limit_remaining = result.remaining
    request.state.rate_limit_limit = result.limit
    request.state.rate_limit_reset_after = result.reset_after

    return api_key


async def require_admin_rate_limit(
    request: Request,
    admin_key: Annotated[APIKey, Depends(require_admin_key)],
    service: Annotated[RateLimitService, Depends(provide_rate_limit_service)],
) -> APIKey:
    settings = get_settings()

    if not settings.rate_limit_enabled:
        return admin_key

    result = service.check_and_record(admin_key)
    request.state.rate_limit_remaining = result.remaining
    request.state.rate_limit_limit = result.limit
    request.state.rate_limit_reset_after = result.reset_after

    return admin_key
