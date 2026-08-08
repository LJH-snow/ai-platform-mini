"""Billing domain models: Plan, Subscription, and resource ceilings."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SubscriptionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    TRIAL = "TRIAL"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


# Only these statuses make a subscription's plan participate in limit
# resolution (quota inheritance + entitlement).  EXPIRED/CANCELLED fall
# back to legacy (no-plan) semantics.
ACTIVE_SUBSCRIPTION_STATUSES = frozenset(
    {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL}
)


@dataclass(frozen=True)
class Plan:
    """One plan definition; NULL limits mean "unlimited" (or inherit)."""

    id: str
    name: str
    version: int = 1
    daily_token_limit: int | None = None
    monthly_token_limit: int | None = None
    max_agents: int | None = None
    max_documents: int | None = None
    max_members: int | None = None
    features: dict[str, bool] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(frozen=True)
class Subscription:
    """A workspace's current subscription (at most one per workspace)."""

    id: str
    workspace_id: str
    plan_id: str
    status: SubscriptionStatus
    started_at: datetime | None = None
    expired_at: datetime | None = None
