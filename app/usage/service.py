import logging
from datetime import UTC, datetime

from app.usage.models import (
    UsageAggregation,
    UsageRanking,
    UsageRecord,
    UsageSummary,
    WorkspaceUsagePoint,
)
from app.usage.repository import UsageRepository

logger = logging.getLogger(__name__)


class UsageService:
    def __init__(self, repository: UsageRepository) -> None:
        self._repository = repository

    async def record(self, usage: UsageRecord) -> None:
        if usage.usage_date is None:
            usage.usage_date = datetime.now(UTC).strftime("%Y-%m-%d")
        if usage.api_key_hash is None:
            usage.api_key_hash = ""
        await self._repository.record_usage(usage)
        logger.info(
            "request_id=%s model=%s prompt=%d completion=%d total=%d latency=%.1fms",
            usage.request_id,
            usage.model,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.total_tokens,
            usage.latency_ms,
        )

    async def get_summary(self, api_key_hash: str) -> UsageSummary:
        return await self._repository.get_summary_for_key(api_key_hash)

    async def get_all_summary(self) -> UsageSummary:
        return await self._repository.get_all_summary()

    async def get_daily_usage(
        self, api_key_hash: str, usage_date: str
    ) -> list[UsageAggregation]:
        return await self._repository.get_daily_usage(api_key_hash, usage_date)

    async def get_monthly_usage(
        self, api_key_hash: str, year_month: str
    ) -> list[UsageAggregation]:
        return await self._repository.get_monthly_usage(api_key_hash, year_month)

    async def get_workspace_trend(
        self, owner_scope: str, days: int
    ) -> list[WorkspaceUsagePoint]:
        return await self._repository.get_workspace_trend(owner_scope, days)

    async def get_workspace_model_ranking(
        self, owner_scope: str, days: int
    ) -> list[UsageRanking]:
        return await self._repository.get_workspace_model_ranking(owner_scope, days)

    async def get_workspace_key_ranking(
        self, owner_scope: str, days: int
    ) -> list[UsageRanking]:
        return await self._repository.get_workspace_key_ranking(owner_scope, days)

    async def get_daily_tokens(self, api_key_hash: str, usage_date: str) -> int:
        return await self._repository.get_total_tokens_for_key(api_key_hash, usage_date)
