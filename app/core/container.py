from functools import lru_cache

from app.core.settings import get_settings
from app.providers.base import LLMProvider
from app.providers.factory import create_llm_provider
from app.ratelimit.base import RateLimiter
from app.ratelimit.memory import MemorySlidingWindowLimiter
from app.ratelimit.service import RateLimitService
from app.usage.service import UsageService


@lru_cache
def provide_llm_provider() -> LLMProvider:
    return create_llm_provider()


@lru_cache
def provide_usage_service() -> UsageService:
    return UsageService()


@lru_cache
def provide_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return MemorySlidingWindowLimiter(
        limit=settings.rate_limit_per_minute,
        window_seconds=60,
    )


@lru_cache
def provide_rate_limit_service() -> RateLimitService:
    return RateLimitService(limiter=provide_rate_limiter())
