"""Billing repository protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.billing.models import Plan, Subscription, SubscriptionStatus


@runtime_checkable
class BillingRepository(Protocol):
    # ── Plans ────────────────────────────────────────────────────────────
    async def seed_plan(self, plan: Plan) -> Plan: ...
    async def list_plans(self) -> list[Plan]: ...
    async def get_plan(self, plan_id: str) -> Plan | None: ...
    # ── Subscriptions ────────────────────────────────────────────────────
    async def create_subscription(self, subscription: Subscription) -> Subscription: ...
    async def update_subscription(
        self, subscription: Subscription
    ) -> Subscription | None: ...
    async def get_subscription_for_workspace(
        self, workspace_id: str
    ) -> Subscription | None: ...
    async def list_subscriptions(
        self,
        *,
        plan_id: str | None = None,
        status: SubscriptionStatus | None = None,
    ) -> list[Subscription]: ...
