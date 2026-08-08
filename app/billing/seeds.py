"""Seed plans for Sprint E1a — idempotent, same location/pattern as
prompt and tool seeds (bootstrapped in app.main._bootstrap_seeds)."""

from __future__ import annotations

import logging

from app.billing.models import Plan
from app.billing.repository import BillingRepository

logger = logging.getLogger(__name__)

# Deterministic ids so tests and future admin flows can reference the
# seeded plans without a lookup round trip.
FREE_PLAN_ID = "10000000-0000-0000-0000-000000000001"
PRO_PLAN_ID = "10000000-0000-0000-0000-000000000002"
ENTERPRISE_PLAN_ID = "10000000-0000-0000-0000-000000000003"


def build_seed_plans() -> list[Plan]:
    """Return the three built-in plans (E1 design A1)."""
    return [
        Plan(
            id=FREE_PLAN_ID,
            name="free",
            monthly_token_limit=100_000,
            max_agents=3,
            max_documents=5,
            max_members=None,
            features={"reranker": False, "benchmark": False},
        ),
        Plan(
            id=PRO_PLAN_ID,
            name="pro",
            monthly_token_limit=10_000_000,
            max_agents=50,
            max_documents=100,
            max_members=None,
            features={"reranker": True, "benchmark": True},
        ),
        Plan(
            id=ENTERPRISE_PLAN_ID,
            name="enterprise",
            # NULL limits = unlimited.
            daily_token_limit=None,
            monthly_token_limit=None,
            max_agents=None,
            max_documents=None,
            max_members=None,
            features={"reranker": True, "benchmark": True},
        ),
    ]


async def seed_billing_plans(repository: BillingRepository) -> None:
    """Idempotently seed built-in plans (by name, never overwrite)."""
    for plan in build_seed_plans():
        await repository.seed_plan(plan)
    logger.info("Billing plan seeds bootstrap complete.")
