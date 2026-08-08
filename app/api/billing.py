"""User-facing Billing endpoint (Sprint E1a): current plan + monthly usage
+ resource counts + features, mirroring the Usage Dashboard auth pattern."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.agent_config.service import AgentDefinitionService
from app.api.auth import provide_workspace_service
from app.auth.identity import IdentityContext
from app.auth.models import APIKey
from app.auth.tenant import resolve_tenant_scope
from app.auth.workspace_service import WorkspaceService
from app.billing.models import ACTIVE_SUBSCRIPTION_STATUSES
from app.billing.service import PlanService
from app.core.container import (
    provide_agent_definition_service,
    provide_plan_service,
    provide_rag_ingestion_service,
    provide_usage_service,
)
from app.exceptions.base import AuthorizationError
from app.rag.ingestion import RAGIngestionService
from app.ratelimit.dependencies import require_rate_limit
from app.usage.models import UsageAggregation
from app.usage.service import UsageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


class PlanView(BaseModel):
    id: str
    name: str
    version: int
    status: str
    features: dict[str, bool] = Field(default_factory=dict)
    daily_token_limit: int | None = None
    monthly_token_limit: int | None = None
    max_agents: int | None = None
    max_documents: int | None = None
    max_members: int | None = None


class UsageView(BaseModel):
    month: str
    total_tokens: int


class ResourceView(BaseModel):
    count: int
    limit: int | None = None


class ResourcesView(BaseModel):
    agents: ResourceView
    documents: ResourceView
    members: ResourceView


class BillingResponse(BaseModel):
    # None = no subscription (legacy, fully open).
    plan: PlanView | None = None
    usage: UsageView
    resources: ResourcesView


def _owner_scope(request: Request) -> str:
    """Same run-record scope semantics: raw workspace id or legacy key hash."""
    identity = cast(IdentityContext | None, request.state.context.identity)
    if identity is None:
        raise AuthorizationError("Identity not resolved.")
    if identity.workspace_id is not None:
        return identity.workspace_id
    return identity.api_key_hash


@router.get("", response_model=BillingResponse)
async def get_billing(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    plan_service: Annotated[PlanService, Depends(provide_plan_service)],
    usage_service: Annotated[UsageService, Depends(provide_usage_service)],
    agent_service: Annotated[
        AgentDefinitionService, Depends(provide_agent_definition_service)
    ],
    workspace_service: Annotated[WorkspaceService, Depends(provide_workspace_service)],
    rag_ingestion: Annotated[
        RAGIngestionService | None, Depends(provide_rag_ingestion_service)
    ],
) -> BillingResponse:
    """Return the authenticated tenant's billing overview.

    Workspace-bound keys see the workspace plan/usage/counts; legacy
    (unbound) keys always see plan=None and their own key-dimension
    usage (documents are scoped by tenant scope in both cases).
    """
    identity: IdentityContext | None = request.state.context.identity
    workspace_id = identity.workspace_id if identity is not None else None
    scope = _owner_scope(request)

    subscription = (
        await plan_service.get_subscription_for_workspace(workspace_id)
        if workspace_id is not None
        else None
    )
    plan = (
        await plan_service.get_plan(subscription.plan_id)
        if subscription is not None
        else None
    )
    plan_active = (
        subscription is not None and subscription.status in ACTIVE_SUBSCRIPTION_STATUSES
    )

    month = datetime.now(UTC).strftime("%Y-%m")
    if workspace_id is not None:
        aggregations = await usage_service.get_monthly_usage_for_workspace(
            workspace_id, month
        )
    else:
        # Legacy scope IS the key hash — monthly usage judged per key.
        aggregations = await usage_service.get_monthly_usage(scope, month)
    usage_total = _total_tokens(aggregations)

    agent_count = (
        len(await agent_service.list_agents(workspace_id))
        if workspace_id is not None
        else 0
    )
    document_count = (
        len(
            await rag_ingestion.list_documents(
                owner_key_hash=resolve_tenant_scope(identity)
            )
        )
        if rag_ingestion is not None
        else 0
    )
    member_count = await _member_count(workspace_service, workspace_id, identity)

    if plan is None:
        return BillingResponse(
            plan=None,
            usage=UsageView(month=month, total_tokens=usage_total),
            resources=ResourcesView(
                agents=ResourceView(count=agent_count),
                documents=ResourceView(count=document_count),
                members=ResourceView(count=member_count),
            ),
        )

    return BillingResponse(
        plan=PlanView(
            id=plan.id,
            name=plan.name,
            version=plan.version,
            status=subscription.status.value,  # type: ignore[union-attr]
            features=plan.features if plan_active else {},
            daily_token_limit=plan.daily_token_limit if plan_active else None,
            monthly_token_limit=plan.monthly_token_limit if plan_active else None,
            max_agents=plan.max_agents if plan_active else None,
            max_documents=plan.max_documents if plan_active else None,
            max_members=plan.max_members if plan_active else None,
        ),
        usage=UsageView(month=month, total_tokens=usage_total),
        resources=ResourcesView(
            agents=ResourceView(
                count=agent_count, limit=plan.max_agents if plan_active else None
            ),
            documents=ResourceView(
                count=document_count, limit=plan.max_documents if plan_active else None
            ),
            members=ResourceView(
                count=member_count, limit=plan.max_members if plan_active else None
            ),
        ),
    )


async def _member_count(
    workspace_service: WorkspaceService,
    workspace_id: str | None,
    identity: IdentityContext | None,
) -> int:
    if workspace_id is None or identity is None or identity.user_id is None:
        return 0
    try:
        members = await workspace_service.list_members(workspace_id, identity.user_id)
    except AuthorizationError:
        # The key is workspace-bound but the user is no longer a member
        # (removed/deleted): report zero instead of failing the billing
        # overview for the whole tenant.
        return 0
    return len(members)


def _total_tokens(aggregations: list[UsageAggregation]) -> int:
    return sum(aggregation.total_tokens for aggregation in aggregations)
