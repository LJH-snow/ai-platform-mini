import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from app.usage.models import UsageAggregation, UsageRecord, UsageSummary

logger = logging.getLogger(__name__)

_MAX_RECORDS = 1000
_MAX_DAILY_DAYS = 90


class InMemoryUsageRepository:
    def __init__(self) -> None:
        self._records: list[UsageRecord] = []
        self._daily: dict[str, dict[str, UsageAggregation]] = defaultdict(dict)

    async def record_usage(self, record: UsageRecord) -> None:
        self._records.append(record)
        if len(self._records) > _MAX_RECORDS:
            self._records = self._records[-_MAX_RECORDS:]

        if record.api_key_hash and record.usage_date:
            key = f"{record.api_key_hash}:{record.usage_date}"
            agg = self._daily[key].setdefault(
                record.model,
                UsageAggregation(model=record.model),
            )
            agg.request_count += 1
            agg.prompt_tokens += record.prompt_tokens
            agg.completion_tokens += record.completion_tokens
            agg.total_tokens += record.total_tokens

        self._cleanup_old_daily()

    async def get_daily_usage(
        self, api_key_hash: str, usage_date: str
    ) -> list[UsageAggregation]:
        key = f"{api_key_hash}:{usage_date}"
        return list(self._daily.get(key, {}).values())

    async def get_monthly_usage(
        self, api_key_hash: str, year_month: str
    ) -> list[UsageAggregation]:
        result: dict[str, UsageAggregation] = {}
        prefix = f"{api_key_hash}:{year_month}"
        for k, aggs in self._daily.items():
            if not k.startswith(prefix):
                continue
            for agg in aggs.values():
                merged = result.setdefault(agg.model, UsageAggregation(model=agg.model))
                merged.request_count += agg.request_count
                merged.prompt_tokens += agg.prompt_tokens
                merged.completion_tokens += agg.completion_tokens
                merged.total_tokens += agg.total_tokens
        return list(result.values())

    async def get_total_tokens_for_key(self, api_key_hash: str, usage_date: str) -> int:
        key = f"{api_key_hash}:{usage_date}"
        aggs = self._daily.get(key, {})
        return sum(a.total_tokens for a in aggs.values())

    @property
    def record_count(self) -> int:
        return len(self._records)

    async def get_all_summary(self) -> UsageSummary:
        return self._summarize(self._records)

    async def get_summary_for_key(self, api_key_hash: str) -> UsageSummary:
        return self._summarize(
            record for record in self._records if record.api_key_hash == api_key_hash
        )

    @staticmethod
    def _summarize(records: Iterable[UsageRecord]) -> UsageSummary:
        summary = UsageSummary()
        for record in records:
            summary.total_requests += 1
            summary.total_prompt_tokens += record.prompt_tokens
            summary.total_completion_tokens += record.completion_tokens
            summary.total_tokens += record.total_tokens
            model_stats = summary.by_model.setdefault(
                record.model, {"requests": 0, "total_tokens": 0}
            )
            model_stats["requests"] += 1
            model_stats["total_tokens"] += record.total_tokens
        return summary

    def _cleanup_old_daily(self) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=_MAX_DAILY_DAYS)).strftime(
            "%Y-%m-%d"
        )
        stale_keys = [k for k in self._daily if k.split(":")[1] < cutoff]
        for k in stale_keys:
            del self._daily[k]
