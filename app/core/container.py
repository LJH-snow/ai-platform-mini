from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.settings import get_settings
from app.providers.base import LLMProvider
from app.providers.factory import create_llm_provider
from app.quota.memory_repository import InMemoryQuotaRepository
from app.quota.models import QuotaConfig
from app.quota.repository import QuotaRepository
from app.quota.service import QuotaService
from app.ratelimit.base import RateLimiter
from app.ratelimit.memory import MemorySlidingWindowLimiter
from app.ratelimit.service import RateLimitService
from app.usage.collector import UsageCollector
from app.usage.memory_repository import InMemoryUsageRepository
from app.usage.repository import UsageRepository
from app.usage.service import UsageService


@lru_cache
def provide_llm_provider() -> LLMProvider:
    return create_llm_provider()


@lru_cache
def provide_session_factory() -> async_sessionmaker[AsyncSession]:
    from app.db.session import create_async_session_factory

    return create_async_session_factory()


@lru_cache
def provide_usage_repository() -> UsageRepository:
    settings = get_settings()
    if settings.auth_storage == "postgres":
        from app.usage.postgres_repository import PostgresUsageRepository

        session_factory = provide_session_factory()
        return PostgresUsageRepository(session_factory)
    return InMemoryUsageRepository()


@lru_cache
def provide_usage_service() -> UsageService:
    return UsageService(repository=provide_usage_repository())


@lru_cache
def provide_usage_collector() -> UsageCollector:
    return UsageCollector(provide_usage_service())


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


@lru_cache
def provide_quota_service() -> QuotaService:
    settings = get_settings()
    config = QuotaConfig(
        daily_token_limit=settings.quota_daily_tokens or None,
        monthly_token_limit=settings.quota_monthly_tokens or None,
        default_reserve_tokens=512,
        reservation_ttl_seconds=settings.quota_reservation_ttl_seconds,
        reservation_renewal_seconds=settings.quota_reservation_renewal_seconds,
    )
    usage_repo = provide_usage_repository()
    if settings.auth_storage == "postgres":
        from app.quota.postgres_repository import PostgresQuotaRepository

        session_factory = provide_session_factory()
        quota_repo: QuotaRepository = PostgresQuotaRepository(
            session_factory, usage_repo
        )
    else:
        quota_repo = InMemoryQuotaRepository(usage_repo)
    return QuotaService(
        usage_repository=usage_repo,
        quota_repository=quota_repo,
        config=config,
    )
