"""PostgreSQL Billing repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.billing.models import Plan, Subscription, SubscriptionStatus
from app.db.billing_models import PlanTable, SubscriptionTable


class PostgresBillingRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def seed_plan(self, plan: Plan) -> Plan:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(PlanTable).where(PlanTable.name == plan.name)
            )
            if existing is not None:
                return _plan_row_to_record(existing)
            row = PlanTable(
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
            session.add(row)
            await session.commit()
            return _plan_row_to_record(row)

    async def list_plans(self) -> list[Plan]:
        async with self._session_factory() as session:
            stmt = select(PlanTable).order_by(PlanTable.created_at)
            rows = await session.scalars(stmt)
            return [_plan_row_to_record(row) for row in rows]

    async def get_plan(self, plan_id: str) -> Plan | None:
        async with self._session_factory() as session:
            row = await session.get(PlanTable, plan_id)
            return _plan_row_to_record(row) if row is not None else None

    async def create_subscription(self, subscription: Subscription) -> Subscription:
        async with self._session_factory() as session:
            row = SubscriptionTable(
                id=subscription.id,
                workspace_id=subscription.workspace_id,
                plan_id=subscription.plan_id,
                status=subscription.status.value,
                started_at=subscription.started_at,
                expired_at=subscription.expired_at,
            )
            session.add(row)
            await session.commit()
            return _subscription_row_to_record(row)

    async def update_subscription(
        self, subscription: Subscription
    ) -> Subscription | None:
        async with self._session_factory() as session:
            row = await session.get(SubscriptionTable, subscription.id)
            if row is None:
                return None
            row.plan_id = subscription.plan_id
            row.status = subscription.status.value
            row.expired_at = subscription.expired_at
            await session.commit()
            return _subscription_row_to_record(row)

    async def get_subscription_for_workspace(
        self, workspace_id: str
    ) -> Subscription | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(SubscriptionTable).where(
                    SubscriptionTable.workspace_id == workspace_id
                )
            )
            return _subscription_row_to_record(row) if row is not None else None

    async def list_subscriptions(
        self,
        *,
        plan_id: str | None = None,
        status: SubscriptionStatus | None = None,
    ) -> list[Subscription]:
        async with self._session_factory() as session:
            stmt = select(SubscriptionTable)
            if plan_id is not None:
                stmt = stmt.where(SubscriptionTable.plan_id == plan_id)
            if status is not None:
                stmt = stmt.where(SubscriptionTable.status == status.value)
            stmt = stmt.order_by(SubscriptionTable.started_at.desc())
            rows = await session.scalars(stmt)
            return [_subscription_row_to_record(row) for row in rows]


def _plan_row_to_record(row: PlanTable) -> Plan:
    return Plan(
        id=row.id,
        name=row.name,
        version=row.version,
        daily_token_limit=row.daily_token_limit,
        monthly_token_limit=row.monthly_token_limit,
        max_agents=row.max_agents,
        max_documents=row.max_documents,
        max_members=row.max_members,
        features=dict(row.features or {}),
        created_at=row.created_at,
    )


def _subscription_row_to_record(row: SubscriptionTable) -> Subscription:
    return Subscription(
        id=row.id,
        workspace_id=row.workspace_id,
        plan_id=row.plan_id,
        status=SubscriptionStatus(row.status),
        started_at=row.started_at,
        expired_at=row.expired_at,
    )
