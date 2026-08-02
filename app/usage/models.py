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


@dataclass
class UsageSummary:
    total_requests: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)
