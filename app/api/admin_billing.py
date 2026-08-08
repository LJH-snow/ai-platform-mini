"""Admin Billing endpoints (Sprint E1a): subscription assignment, list, plans.

Follows the admin quota endpoint pattern: require_admin_rate_limit,
workspace existence → 404, explicit admin key errors."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.auth import provide_workspace_service
from app.auth.models import APIKey
from app.auth.workspace_service import WorkspaceService
from app.billing.models import Plan, Subscription, SubscriptionStatus
from app.billing.service import PlanService
from app.core.container import provide_plan_service
from app.ratelimit.dependencies import require_admin_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class AssignSubscriptionRequest(BaseModel):
    plan_id: str
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE


class SubscriptionResponse(BaseModel):
    id: str
    workspace_id: str
    plan_id: str
    plan_name: str | None = None
    status: str
    started_at: datetime | None = None
    expired_at: datetime | None = None


class PlanAdminResponse(BaseModel):
    id: str
    name: str
    version: int
    daily_token_limit: int | None = None
    monthly_token_limit: int | None = None
    max_agents: int | None = None
    max_documents: int | None = None
    max_members: int | None = None
    features: dict[str, bool]


def _plan_response(plan: Plan) -> PlanAdminResponse:
    return PlanAdminResponse(
        id=plan.id,
        name=plan.name,
        version=plan.version,
        daily_token_limit=plan.daily_token_limit,
        monthly_token_limit=plan.monthly_token_limit,
        max_agents=plan.max_agents,
        max_documents=plan.max_documents,
        max_members=plan.max_members,
        features=plan.features,
    )


def _subscription_response(
    subscription: Subscription, plan: Plan | None
) -> SubscriptionResponse:
    return SubscriptionResponse(
        id=subscription.id,
        workspace_id=subscription.workspace_id,
        plan_id=subscription.plan_id,
        plan_name=plan.name if plan is not None else None,
        status=subscription.status.value,
        started_at=subscription.started_at,
        expired_at=subscription.expired_at,
    )


@router.post(
    "/workspaces/{workspace_id}/subscription",
    response_model=SubscriptionResponse,
    summary="Assign or change a workspace's subscription plan",
)
async def assign_subscription(
    workspace_id: str,
    body: AssignSubscriptionRequest,
    _admin: Annotated[APIKey, Depends(require_admin_rate_limit)],
    plan_service: Annotated[PlanService, Depends(provide_plan_service)],
    workspace_service: Annotated[WorkspaceService, Depends(provide_workspace_service)],
) -> SubscriptionResponse:
    if await workspace_service.get_workspace(workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    if await plan_service.get_plan(body.plan_id) is None:
        raise HTTPException(status_code=404, detail="Plan not found.")
    subscription = await plan_service.assign_plan(
        workspace_id, body.plan_id, status=body.status
    )
    plan = await plan_service.get_plan(subscription.plan_id)
    logger.info(
        "subscription_assigned workspace=%s plan=%s status=%s",
        workspace_id,
        body.plan_id,
        body.status.value,
    )
    return _subscription_response(subscription, plan)


@router.get(
    "/subscriptions",
    response_model=list[SubscriptionResponse],
    summary="List subscriptions (filter by plan/status)",
)
async def list_subscriptions(
    _admin: Annotated[APIKey, Depends(require_admin_rate_limit)],
    plan_service: Annotated[PlanService, Depends(provide_plan_service)],
    plan_id: Annotated[str | None, Query] = None,
    status: Annotated[SubscriptionStatus | None, Query] = None,
) -> list[SubscriptionResponse]:
    subscriptions = await plan_service.list_subscriptions(
        plan_id=plan_id, status=status
    )
    result: list[SubscriptionResponse] = []
    for subscription in subscriptions:
        plan = await plan_service.get_plan(subscription.plan_id)
        result.append(_subscription_response(subscription, plan))
    return result


@router.get(
    "/plans",
    response_model=list[PlanAdminResponse],
    summary="List all subscription plans",
)
async def list_plans(
    _admin: Annotated[APIKey, Depends(require_admin_rate_limit)],
    plan_service: Annotated[PlanService, Depends(provide_plan_service)],
) -> list[PlanAdminResponse]:
    plans = await plan_service.list_plans()
    return [_plan_response(plan) for plan in plans]
