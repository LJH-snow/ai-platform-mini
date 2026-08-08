"""EntitlementService: feature capabilities + resource ceilings (E1a).

Responsibilities are split deliberately (review correction):
- feature capability  → ``check_feature``  (reranker / benchmark / ...)
- resource ceiling    → ``check_limit``   (agent / document / member)

Token limits stay with QuotaResolver — this service never reads them.
Legacy semantics (not negotiable): a workspace WITHOUT a subscription is
fully open — check_feature returns True and check_limit returns True.
A subscription is an explicit tightening operation.
"""

from __future__ import annotations

import logging

from app.billing.models import (
    ACTIVE_SUBSCRIPTION_STATUSES,
    Plan,
)
from app.billing.repository import BillingRepository
from app.exceptions.base import ValidationError

logger = logging.getLogger(__name__)

# Resource name → plan ceiling field.  Only the three agreed checkpoints
# (create_agent / document ingestion / member add) are wired; the mapping
# is closed so an unknown resource fails loudly instead of silently
# passing an unenforced ceiling.
_RESOURCE_LIMIT_FIELDS: dict[str, str] = {
    "agent": "max_agents",
    "document": "max_documents",
    "member": "max_members",
}


class EntitlementService:
    def __init__(self, repository: BillingRepository) -> None:
        self._repo = repository

    async def check_feature(self, workspace_id: str, feature: str) -> bool:
        """Return whether the workspace's plan enables a feature.

        No subscription → True (legacy: fully open).
        """
        plan = await self._plan_for_workspace(workspace_id)
        if plan is None:
            return True
        return bool(plan.features.get(feature, False))

    async def check_limit(
        self, workspace_id: str, resource: str, current_count: int
    ) -> bool:
        """Return whether ``current_count`` is below the plan's ceiling.

        No subscription → True (unlimited).  A NULL ceiling → True.
        """
        plan = await self._plan_for_workspace(workspace_id)
        if plan is None:
            return True
        limit = self._limit_for(plan, resource)
        if limit is None:
            return True
        return current_count < limit

    async def require_limit(
        self, workspace_id: str, resource: str, current_count: int
    ) -> None:
        """Raise ValidationError when the ceiling would be exceeded.

        Message carries the plan name and ceiling so callers surface a
        precise 422 ("已达 {plan} 计划上限（{resource} {limit}）。").
        """
        plan = await self._plan_for_workspace(workspace_id)
        if plan is None:
            return
        limit = self._limit_for(plan, resource)
        if limit is None or current_count < limit:
            return
        raise ValidationError(f"已达 {plan.name} 计划上限（{resource} {limit}）。")

    async def _plan_for_workspace(self, workspace_id: str) -> Plan | None:
        """The effective plan, or None for legacy (no subscription).

        Only ACTIVE/TRIAL subscriptions participate; EXPIRED/CANCELLED
        fall back to legacy semantics.  A subscription referencing a
        deleted plan degrades to "no limits" — consistent with NULL
        ceilings meaning unlimited.
        """
        subscription = await self._repo.get_subscription_for_workspace(workspace_id)
        if (
            subscription is None
            or subscription.status not in ACTIVE_SUBSCRIPTION_STATUSES
        ):
            return None
        return await self._repo.get_plan(subscription.plan_id)

    @staticmethod
    def _limit_for(plan: Plan, resource: str) -> int | None:
        field = _RESOURCE_LIMIT_FIELDS.get(resource)
        if field is None:
            raise ValueError(f"Unknown resource ceiling: {resource!r}")
        limit = getattr(plan, field)
        return limit if isinstance(limit, int) else None
