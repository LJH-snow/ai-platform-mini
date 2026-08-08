import logging
import re
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.auth import provide_workspace_service
from app.audit.service import AuditService
from app.auth.dependencies import (
    is_configured_admin_key_hash,
    is_configured_admin_key_prefix,
    provide_api_key_service,
)
from app.auth.models import APIKey, APIKeyMetadata
from app.auth.service import APIKeyService
from app.auth.workspace_service import WorkspaceService
from app.core.container import (
    provide_agent_run_record_service,
    provide_audit_service,
    provide_quota_service,
    provide_usage_service,
)
from app.core.context import RequestContext
from app.exceptions.base import (
    APIKeyNotFoundError,
    AuthorizationError,
    ValidationError,
)
from app.quota.service import QuotaService
from app.ratelimit.dependencies import require_admin_rate_limit
from app.schemas.admin import (
    AgentRunRecordResponse,
    AgentRunRecordSummary,
    APIKeyMetadataResponse,
    CreateAPIKeyRequest,
    CreateAPIKeyResponse,
    RevokeAPIKeyResponse,
    UsageAggregationResponse,
)
from app.services.agent_run_record_service import (
    AgentRunRecordService,
    public_run_payload,
    public_run_summary,
)
from app.usage.service import UsageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _validate_date(value: str, field_name: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValidationError(
            f"Invalid {field_name} format: '{value}'. Expected YYYY-MM-DD."
        ) from None


def _validate_year_month(value: str) -> None:
    if re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", value) is None:
        raise ValidationError(f"Invalid month format: '{value}'. Expected YYYY-MM.")

    year, month = value.split("-")
    try:
        date(int(year), int(month), 1)
    except ValueError:
        raise ValidationError(
            f"Invalid month format: '{value}'. Expected YYYY-MM."
        ) from None


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
            is_admin=is_configured_admin_key_prefix(k.key_hash_prefix),
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
    if is_configured_admin_key_hash(target_hash):
        raise AuthorizationError(
            "Configured administrator API keys cannot be revoked from this endpoint."
        )

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


@router.get(
    "/usage/daily",
    response_model=list[UsageAggregationResponse],
    summary="Get daily usage for an API key",
)
async def get_daily_usage(
    auth_service: Annotated[APIKeyService, Depends(provide_api_key_service)],
    _admin: Annotated[APIKey, Depends(require_admin_rate_limit)],
    usage_service: Annotated[UsageService, Depends(provide_usage_service)],
    key_hash_prefix: str = Query(..., description="API key hash prefix (8 hex chars)"),
    date: str = Query(..., description="Usage date in YYYY-MM-DD format"),
) -> list[UsageAggregationResponse]:
    _validate_date(date, "date")
    target_hash = await auth_service.find_hash_by_prefix(key_hash_prefix)
    if target_hash is None:
        raise APIKeyNotFoundError(f"API key with prefix '{key_hash_prefix}' not found.")
    aggs = await usage_service.get_daily_usage(target_hash, date)
    return [
        UsageAggregationResponse(
            model=a.model,
            request_count=a.request_count,
            prompt_tokens=a.prompt_tokens,
            completion_tokens=a.completion_tokens,
            total_tokens=a.total_tokens,
        )
        for a in aggs
    ]


@router.get(
    "/usage/monthly",
    response_model=list[UsageAggregationResponse],
    summary="Get monthly usage for an API key",
)
async def get_monthly_usage(
    auth_service: Annotated[APIKeyService, Depends(provide_api_key_service)],
    _admin: Annotated[APIKey, Depends(require_admin_rate_limit)],
    usage_service: Annotated[UsageService, Depends(provide_usage_service)],
    key_hash_prefix: str = Query(..., description="API key hash prefix (8 hex chars)"),
    month: str = Query(..., description="Month in YYYY-MM format"),
) -> list[UsageAggregationResponse]:
    _validate_year_month(month)
    target_hash = await auth_service.find_hash_by_prefix(key_hash_prefix)
    if target_hash is None:
        raise APIKeyNotFoundError(f"API key with prefix '{key_hash_prefix}' not found.")
    aggs = await usage_service.get_monthly_usage(target_hash, month)
    return [
        UsageAggregationResponse(
            model=a.model,
            request_count=a.request_count,
            prompt_tokens=a.prompt_tokens,
            completion_tokens=a.completion_tokens,
            total_tokens=a.total_tokens,
        )
        for a in aggs
    ]


@router.get(
    "/agent-runs",
    response_model=list[AgentRunRecordSummary],
    summary="List persisted Agent Run and RAG records",
)
async def list_agent_runs(
    _admin: Annotated[APIKey, Depends(require_admin_rate_limit)],
    record_service: Annotated[
        AgentRunRecordService | None, Depends(provide_agent_run_record_service)
    ],
    limit: int = Query(50, ge=1, le=200),
    status: str | None = Query(None, max_length=32),
) -> list[AgentRunRecordSummary]:
    if record_service is None:
        return []
    rows = await record_service.list_runs(limit=limit, status=status)
    return [public_run_summary(public_run_payload(row)) for row in rows]


@router.get(
    "/agent-runs/{run_id}",
    response_model=AgentRunRecordResponse,
    summary="Get one persisted Agent Run and RAG record",
)
async def get_agent_run(
    run_id: str,
    _admin: Annotated[APIKey, Depends(require_admin_rate_limit)],
    record_service: Annotated[
        AgentRunRecordService | None, Depends(provide_agent_run_record_service)
    ],
) -> AgentRunRecordResponse:
    if record_service is None:
        raise HTTPException(
            status_code=503, detail="Agent Run records are unavailable."
        )
    row = await record_service.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Agent Run record not found.")
    payload = public_run_payload(row)
    return AgentRunRecordResponse(**payload)


class WorkspaceQuotaResponse(BaseModel):
    workspace_id: str
    daily_token_limit: int | None = None
    monthly_token_limit: int | None = None


class WorkspaceQuotaUpdate(BaseModel):
    daily_token_limit: int | None = Field(default=None, ge=0)
    monthly_token_limit: int | None = Field(default=None, ge=0)


@router.get(
    "/workspaces/{workspace_id}/quota",
    response_model=WorkspaceQuotaResponse,
    summary="Read a workspace's quota overrides",
)
async def get_workspace_quota(
    workspace_id: str,
    _admin: Annotated[APIKey, Depends(require_admin_rate_limit)],
    quota_service: Annotated[QuotaService, Depends(provide_quota_service)],
    workspace_service: Annotated[WorkspaceService, Depends(provide_workspace_service)],
) -> WorkspaceQuotaResponse:
    if await workspace_service.get_workspace(workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    quota = await quota_service.get_workspace_quota(workspace_id)
    if quota is None:
        # No row = full inheritance; null/null is an expressible state.
        return WorkspaceQuotaResponse(workspace_id=workspace_id)
    return WorkspaceQuotaResponse(
        workspace_id=quota.workspace_id,
        daily_token_limit=quota.daily_token_limit,
        monthly_token_limit=quota.monthly_token_limit,
    )


@router.put(
    "/workspaces/{workspace_id}/quota",
    response_model=WorkspaceQuotaResponse,
    summary="Write a workspace's quota overrides (null clears a dimension)",
)
async def set_workspace_quota(
    workspace_id: str,
    body: WorkspaceQuotaUpdate,
    _admin: Annotated[APIKey, Depends(require_admin_rate_limit)],
    quota_service: Annotated[QuotaService, Depends(provide_quota_service)],
    workspace_service: Annotated[WorkspaceService, Depends(provide_workspace_service)],
) -> WorkspaceQuotaResponse:
    if await workspace_service.get_workspace(workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    quota = await quota_service.set_workspace_quota(
        workspace_id,
        daily_token_limit=body.daily_token_limit,
        monthly_token_limit=body.monthly_token_limit,
    )
    return WorkspaceQuotaResponse(
        workspace_id=quota.workspace_id,
        daily_token_limit=quota.daily_token_limit,
        monthly_token_limit=quota.monthly_token_limit,
    )


class AuditEventResponse(BaseModel):
    id: int
    workspace_id: str | None = None
    api_key_hash: str | None = None
    user_id: str | None = None
    action: str
    resource_type: str
    resource_id: str
    before: dict[str, object] | None = None
    after: dict[str, object] | None = None
    ip: str | None = None
    created_at: datetime | None = None


@router.get(
    "/audit-events",
    response_model=list[AuditEventResponse],
    summary="List audit events (admin, time-descending)",
)
async def list_audit_events(
    _admin: Annotated[APIKey, Depends(require_admin_rate_limit)],
    audit_service: Annotated[AuditService, Depends(provide_audit_service)],
    workspace_id: str | None = Query(default=None, max_length=64),
    action: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AuditEventResponse]:
    events = await audit_service.list_events(
        workspace_id=workspace_id, action=action, limit=limit
    )
    return [
        AuditEventResponse(
            id=event.id,
            workspace_id=event.workspace_id,
            api_key_hash=event.api_key_hash,
            user_id=event.user_id,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            before=event.before,
            after=event.after,
            ip=event.ip,
            created_at=event.created_at,
        )
        for event in events
    ]
