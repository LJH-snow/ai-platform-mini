from typing import Protocol, runtime_checkable

from app.usage.models import (
    UsageAggregation,
    UsageRanking,
    UsageRecord,
    UsageSummary,
    WorkspaceUsagePoint,
)


@runtime_checkable
class UsageRepository(Protocol):
    async def record_usage(self, record: UsageRecord) -> None: ...

    async def get_daily_usage(
        self, api_key_hash: str, usage_date: str
    ) -> list[UsageAggregation]: ...

    async def get_monthly_usage(
        self, api_key_hash: str, year_month: str
    ) -> list[UsageAggregation]: ...

    async def get_total_tokens_for_key(
        self, api_key_hash: str, usage_date: str
    ) -> int: ...

    async def get_summary_for_key(self, api_key_hash: str) -> UsageSummary: ...

    async def get_all_summary(self) -> UsageSummary: ...

    async def get_total_tokens_for_workspace(
        self, workspace_id: str, usage_date: str
    ) -> int: ...

    async def get_monthly_usage_for_workspace(
        self, workspace_id: str, year_month: str
    ) -> list[UsageAggregation]: ...

    async def get_workspace_trend(
        self, owner_scope: str, days: int
    ) -> list[WorkspaceUsagePoint]: ...

    async def get_workspace_model_ranking(
        self, owner_scope: str, days: int
    ) -> list[UsageRanking]: ...

    async def get_workspace_key_ranking(
        self, owner_scope: str, days: int
    ) -> list[UsageRanking]: ...
