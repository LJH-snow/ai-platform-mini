import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import provide_api_key_service
from app.auth.models import APIKey, APIKeyMetadata
from app.auth.service import APIKeyService
from app.core.context import RequestContext
from app.exceptions.base import APIKeyNotFoundError
from app.ratelimit.dependencies import require_admin_rate_limit
from app.schemas.admin import (
    APIKeyMetadataResponse,
    CreateAPIKeyRequest,
    CreateAPIKeyResponse,
    RevokeAPIKeyResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post(
    "/api-keys",
    response_model=CreateAPIKeyResponse,
    summary="Create a new API key",
)
async def create_api_key(
    body: CreateAPIKeyRequest,
    request: Request,
    service: Annotated[APIKeyService, Depends(provide_api_key_service)],
    _admin: Annotated[APIKey, Depends(require_admin_rate_limit)],
) -> CreateAPIKeyResponse:
    context: RequestContext = request.state.context
    metadata, raw_key = await service.create_key(body.name)
    logger.info(
        "api_key_created name=%s admin=%s request_id=%s",
        body.name,
        _admin.name,
        context.request_id,
    )
    return CreateAPIKeyResponse(
        key_hash_prefix=metadata.key_hash_prefix,
        name=metadata.name,
        raw_key=raw_key,
        created_at=metadata.created_at,
    )


@router.get(
    "/api-keys",
    response_model=list[APIKeyMetadataResponse],
    summary="List all API keys",
)
async def list_api_keys(
    service: Annotated[APIKeyService, Depends(provide_api_key_service)],
    _admin: Annotated[APIKey, Depends(require_admin_rate_limit)],
) -> list[APIKeyMetadataResponse]:
    keys: list[APIKeyMetadata] = await service.list_keys()
    return [
        APIKeyMetadataResponse(
            key_hash_prefix=k.key_hash_prefix,
            name=k.name,
            status=k.status,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
        )
        for k in keys
    ]


@router.delete(
    "/api-keys/{key_hash_prefix}",
    response_model=RevokeAPIKeyResponse,
    summary="Revoke an API key",
)
async def revoke_api_key(
    key_hash_prefix: str,
    request: Request,
    service: Annotated[APIKeyService, Depends(provide_api_key_service)],
    _admin: Annotated[APIKey, Depends(require_admin_rate_limit)],
) -> RevokeAPIKeyResponse:
    context: RequestContext = request.state.context
    target_hash = await service.find_hash_by_prefix(key_hash_prefix)

    if target_hash is None:
        raise APIKeyNotFoundError(f"API key with prefix '{key_hash_prefix}' not found.")

    revoked = await service.revoke_key(target_hash)
    logger.info(
        "api_key_revoked prefix=%s admin=%s request_id=%s",
        key_hash_prefix,
        _admin.name,
        context.request_id,
    )
    return RevokeAPIKeyResponse(
        key_hash_prefix=key_hash_prefix,
        revoked=revoked,
    )
