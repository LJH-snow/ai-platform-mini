import logging
from collections import deque

from app.usage.models import UsageRecord, UsageSummary

logger = logging.getLogger(__name__)

_MAX_RECORDS = 1000


class UsageService:
    def __init__(self) -> None:
        self._records: deque[UsageRecord] = deque(maxlen=_MAX_RECORDS)

    def record(self, usage: UsageRecord) -> None:
        self._records.append(usage)
        logger.info(
            "request_id=%s model=%s prompt=%d completion=%d total=%d latency=%.1fms",
            usage.request_id,
            usage.model,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.total_tokens,
            usage.latency_ms,
        )

    def get_summary(self) -> UsageSummary:
        summary = UsageSummary()
        summary.total_requests = len(self._records)

        for record in self._records:
            summary.total_prompt_tokens += record.prompt_tokens
            summary.total_completion_tokens += record.completion_tokens
            summary.total_tokens += record.total_tokens

            model_stats = summary.by_model.setdefault(
                record.model, {"requests": 0, "total_tokens": 0}
            )
            model_stats["requests"] += 1
            model_stats["total_tokens"] += record.total_tokens

        return summary

    @property
    def record_count(self) -> int:
        return len(self._records)
