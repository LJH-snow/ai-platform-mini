"""In-memory Billing repository (default backend for tests / memory mode)."""

from __future__ import annotations

from app.billing.models import Plan, Subscription, SubscriptionStatus


class InMemoryBillingRepository:
    def __init__(self, plans: list[Plan] | None = None) -> None:
        self._plans_by_id: dict[str, Plan] = {}
        self._plans_by_name: dict[str, Plan] = {}
        for plan in plans or []:
            self._plans_by_id[plan.id] = plan
            self._plans_by_name[plan.name] = plan
        self._subscriptions_by_id: dict[str, Subscription] = {}
        self._subscription_workspace_ids: dict[str, str] = {}

    async def seed_plan(self, plan: Plan) -> Plan:
        """Idempotent upsert-by-name: a seeded plan name is never replaced."""
        existing = self._plans_by_name.get(plan.name)
        if existing is not None:
            return existing
        self._plans_by_id[plan.id] = plan
        self._plans_by_name[plan.name] = plan
        return plan

    async def list_plans(self) -> list[Plan]:
        return list(self._plans_by_id.values())

    async def get_plan(self, plan_id: str) -> Plan | None:
        return self._plans_by_id.get(plan_id)

    async def create_subscription(self, subscription: Subscription) -> Subscription:
        self._subscriptions_by_id[subscription.id] = subscription
        self._subscription_workspace_ids[subscription.workspace_id] = subscription.id
        return subscription

    async def update_subscription(
        self, subscription: Subscription
    ) -> Subscription | None:
        if subscription.id not in self._subscriptions_by_id:
            return None
        self._subscriptions_by_id[subscription.id] = subscription
        self._subscription_workspace_ids[subscription.workspace_id] = subscription.id
        return subscription

    async def get_subscription_for_workspace(
        self, workspace_id: str
    ) -> Subscription | None:
        subscription_id = self._subscription_workspace_ids.get(workspace_id)
        if subscription_id is None:
            return None
        return self._subscriptions_by_id.get(subscription_id)

    async def list_subscriptions(
        self,
        *,
        plan_id: str | None = None,
        status: SubscriptionStatus | None = None,
    ) -> list[Subscription]:
        result: list[Subscription] = []
        for subscription in self._subscriptions_by_id.values():
            if plan_id is not None and subscription.plan_id != plan_id:
                continue
            if status is not None and subscription.status != status:
                continue
            result.append(subscription)
        return result
