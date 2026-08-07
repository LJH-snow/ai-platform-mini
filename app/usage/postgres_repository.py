import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import DailyUsageTable
from app.usage.models import (
    UsageAggregation,
    UsageRanking,
    UsageRecord,
    UsageSummary,
    WorkspaceUsagePoint,
)

logger = logging.getLogger(__name__)


class PostgresUsageRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record_usage(self, record: UsageRecord) -> None:
        if not record.api_key_hash or not record.usage_date:
            logger.debug("Skipping usage record without key_hash or date.")
            return

        async with self._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO daily_usage "
                    "(api_key_hash, workspace_id, usage_date, model, "
                    "request_count, prompt_tokens, completion_tokens, "
                    "total_tokens) "
                    "VALUES (:hash, :workspace_id, :date, :model, 1, "
                    ":prompt, :completion, :total) "
                    "ON CONFLICT (api_key_hash, usage_date, model) DO UPDATE SET "
                    "request_count = daily_usage.request_count + 1, "
                    "prompt_tokens = daily_usage.prompt_tokens + :prompt, "
                    "completion_tokens = daily_usage.completion_tokens + "
                    ":completion, "
                    "total_tokens = daily_usage.total_tokens + :total"
                ),
                {
                    "hash": record.api_key_hash,
                    "workspace_id": record.workspace_id,
                    "date": date.fromisoformat(record.usage_date),
                    "model": record.model,
                    "prompt": record.prompt_tokens,
                    "completion": record.completion_tokens,
                    "total": record.total_tokens,
                },
            )
            await session.commit()

    async def get_daily_usage(
        self, api_key_hash: str, usage_date: str
    ) -> list[UsageAggregation]:
        async with self._session_factory() as session:
            stmt = select(DailyUsageTable).where(
                DailyUsageTable.api_key_hash == api_key_hash,
                DailyUsageTable.usage_date == date.fromisoformat(usage_date),
            )
            rows = await session.scalars(stmt)
            return [_row_to_agg(row) for row in rows]

    async def get_monthly_usage(
        self, api_key_hash: str, year_month: str
    ) -> list[UsageAggregation]:
        year, month = year_month.split("-")
        start = f"{year}-{month}-01"
        if month == "12":
            end = f"{int(year) + 1}-01-01"
        else:
            end = f"{year}-{int(month) + 1:02d}-01"

        async with self._session_factory() as session:
            stmt = (
                select(DailyUsageTable)
                .where(
                    DailyUsageTable.api_key_hash == api_key_hash,
                    DailyUsageTable.usage_date >= date.fromisoformat(start),
                    DailyUsageTable.usage_date < date.fromisoformat(end),
                )
                .order_by(DailyUsageTable.usage_date)
            )
            rows = await session.scalars(stmt)

            merged: dict[str, UsageAggregation] = {}
            for row in rows:
                agg = merged.setdefault(row.model, UsageAggregation(model=row.model))
                agg.request_count += row.request_count
                agg.prompt_tokens += row.prompt_tokens
                agg.completion_tokens += row.completion_tokens
                agg.total_tokens += row.total_tokens
            return list(merged.values())

    async def get_total_tokens_for_key(self, api_key_hash: str, usage_date: str) -> int:
        async with self._session_factory() as session:
            stmt = select(DailyUsageTable).where(
                DailyUsageTable.api_key_hash == api_key_hash,
                DailyUsageTable.usage_date == date.fromisoformat(usage_date),
            )
            rows = await session.scalars(stmt)
            return sum(row.total_tokens for row in rows)

    async def get_all_summary(self) -> UsageSummary:
        async with self._session_factory() as session:
            stmt = select(DailyUsageTable)
            rows = await session.scalars(stmt)
            return self._summarize(rows)

    async def get_workspace_trend(
        self, owner_scope: str, days: int
    ) -> list[WorkspaceUsagePoint]:
        """Aggregate total tokens and requests per day for one tenant scope."""
        start = _utc_today() - timedelta(days=days - 1)
        async with self._session_factory() as session:
            stmt = (
                select(
                    DailyUsageTable.usage_date,
                    func.sum(DailyUsageTable.total_tokens).label("total_tokens"),
                    func.sum(DailyUsageTable.request_count).label("request_count"),
                )
                .where(
                    or_(
                        DailyUsageTable.workspace_id == owner_scope,
                        and_(
                            DailyUsageTable.workspace_id.is_(None),
                            DailyUsageTable.api_key_hash == owner_scope,
                        ),
                    ),
                    DailyUsageTable.usage_date >= start,
                )
                .group_by(DailyUsageTable.usage_date)
                .order_by(DailyUsageTable.usage_date)
            )
            rows = await session.execute(stmt)
            return [
                WorkspaceUsagePoint(
                    usage_date=row.usage_date.isoformat(),
                    total_tokens=int(row.total_tokens or 0),
                    request_count=int(row.request_count or 0),
                )
                for row in rows.all()
            ]

    async def get_workspace_model_ranking(
        self, owner_scope: str, days: int
    ) -> list[UsageRanking]:
        return await self._workspace_ranking(owner_scope, days, "model")

    async def get_workspace_key_ranking(
        self, owner_scope: str, days: int
    ) -> list[UsageRanking]:
        return await self._workspace_ranking(owner_scope, days, "key")

    async def _workspace_ranking(
        self, owner_scope: str, days: int, dimension: str
    ) -> list[UsageRanking]:
        start = _utc_today() - timedelta(days=days - 1)
        column = (
            DailyUsageTable.model
            if dimension == "model"
            else DailyUsageTable.api_key_hash
        )
        async with self._session_factory() as session:
            stmt = (
                select(
                    column.label("name"),
                    func.sum(DailyUsageTable.total_tokens).label("total_tokens"),
                    func.sum(DailyUsageTable.request_count).label("request_count"),
                )
                .where(
                    or_(
                        DailyUsageTable.workspace_id == owner_scope,
                        and_(
                            DailyUsageTable.workspace_id.is_(None),
                            DailyUsageTable.api_key_hash == owner_scope,
                        ),
                    ),
                    DailyUsageTable.usage_date >= start,
                )
                .group_by(column)
                .order_by(func.sum(DailyUsageTable.total_tokens).desc())
            )
            rows = await session.execute(stmt)
            rankings = [
                UsageRanking(
                    name=str(row.name),
                    total_tokens=int(row.total_tokens or 0),
                    request_count=int(row.request_count or 0),
                )
                for row in rows.all()
            ]
            if dimension == "key":
                for ranking in rankings:
                    ranking.name = ranking.name[:8]
            return rankings

    async def get_summary_for_key(self, api_key_hash: str) -> UsageSummary:
        async with self._session_factory() as session:
            stmt = select(DailyUsageTable).where(
                DailyUsageTable.api_key_hash == api_key_hash
            )
            rows = await session.scalars(stmt)
            return self._summarize(rows)

    @staticmethod
    def _summarize(rows: Iterable[DailyUsageTable]) -> UsageSummary:
        summary = UsageSummary()
        for row in rows:
            summary.total_requests += row.request_count
            summary.total_prompt_tokens += row.prompt_tokens
            summary.total_completion_tokens += row.completion_tokens
            summary.total_tokens += row.total_tokens
            model_stats = summary.by_model.setdefault(
                row.model, {"requests": 0, "total_tokens": 0}
            )
            model_stats["requests"] += row.request_count
            model_stats["total_tokens"] += row.total_tokens
        return summary


def _row_to_agg(row: DailyUsageTable) -> UsageAggregation:
    return UsageAggregation(
        model=row.model,
        request_count=row.request_count,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        total_tokens=row.total_tokens,
    )


def _utc_today() -> date:
    """UTC calendar day — the write path records usage_date in UTC."""
    return datetime.now(UTC).date()
