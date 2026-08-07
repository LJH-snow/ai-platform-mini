from dataclasses import dataclass, field


@dataclass
class UsageRecord:
    request_id: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    api_key_name: str | None = None
    api_key_hash: str | None = None
    workspace_id: str | None = None
    usage_date: str | None = None


@dataclass
class UsageAggregation:
    model: str
    request_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class WorkspaceUsagePoint:
    """One day of aggregated usage for the workspace trend."""

    usage_date: str
    total_tokens: int = 0
    request_count: int = 0


@dataclass
class UsageRanking:
    """One ranked dimension (model or key) for the dashboard."""

    name: str
    total_tokens: int = 0
    request_count: int = 0


@dataclass
class UsageSummary:
    total_requests: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)
