import logging

from app.auth.models import APIKey
from app.exceptions.base import RateLimitError
from app.ratelimit.base import RateLimiter, RateLimitResult

logger = logging.getLogger(__name__)


class RateLimitService:
    def __init__(self, limiter: RateLimiter) -> None:
        self._limiter = limiter

    def check_and_record(self, api_key: APIKey) -> RateLimitResult:
        result = self._limiter.acquire(api_key.key)

        if not result.allowed:
            logger.warning(
                "rate_limit_exceeded api_key=%s limit=%d reset_after=%ds",
                api_key.name,
                result.limit,
                result.reset_after,
            )
            raise RateLimitError(
                f"Rate limit exceeded. Limit: {result.limit} requests per minute. "
                f"Retry after {result.reset_after}s."
            )

        return result
