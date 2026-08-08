"""PlanService: subscription assignment and lifecycle."""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime

from app.billing.models import (
    ACTIVE_SUBSCRIPTION_STATUSES,
    Plan,
    Subscription,
    SubscriptionStatus,
)
from app.billing.repository import BillingRepository
from app.exceptions.base import ValidationError

logger = logging.getLogger(__name__)


class PlanService:
    def __init__(self, repository: BillingRepository) -> None:
        self._repo = repository

    async def get_plan(self, plan_id: str) -> Plan | None:
        return await self._repo.get_plan(plan_id)

    async def list_plans(self) -> list[Plan]:
        return await self._repo.list_plans()

    async def get_subscription_for_workspace(
        self, workspace_id: str
    ) -> Subscription | None:
        return await self._repo.get_subscription_for_workspace(workspace_id)

    async def list_subscriptions(
        self,
        *,
        plan_id: str | None = None,
        status: SubscriptionStatus | None = None,
    ) -> list[Subscription]:
        return await self._repo.list_subscriptions(plan_id=plan_id, status=status)

    async def assign_plan(
        self,
        workspace_id: str,
        plan_id: str,
        *,
        status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    ) -> Subscription:
        """Assign a plan: create the subscription when absent, else update it.

        Assigning is an explicit tightening/relaxing operation — the
        legacy (no-subscription) state is never touched implicitly.
        """
        plan = await self._repo.get_plan(plan_id)
        if plan is None:
            raise ValidationError(f"Plan '{plan_id}' not found.")

        now = datetime.now(UTC)
        existing = await self._repo.get_subscription_for_workspace(workspace_id)
        if existing is None:
            subscription = Subscription(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                plan_id=plan_id,
                status=status,
                started_at=now,
                expired_at=None if status in ACTIVE_SUBSCRIPTION_STATUSES else now,
            )
            saved = await self._repo.create_subscription(subscription)
            logger.info(
                "subscription_created workspace=%s plan=%s status=%s",
                workspace_id,
                plan_id,
                status.value,
            )
            return saved

        # Keep the original started_at; only plan/status/expiry refresh.
        expired_at = existing.expired_at
        if status in ACTIVE_SUBSCRIPTION_STATUSES:
            expired_at = None
        elif expired_at is None:
            expired_at = now
        updated = replace(
            existing,
            plan_id=plan_id,
            status=status,
            expired_at=expired_at,
        )
        updated_result = await self._repo.update_subscription(updated)
        if updated_result is None:
            raise ValidationError("Subscription no longer exists.")
        logger.info(
            "subscription_updated workspace=%s plan=%s status=%s",
            workspace_id,
            plan_id,
            status.value,
        )
        return updated_result
