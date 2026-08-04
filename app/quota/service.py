import logging
import uuid
from datetime import UTC, datetime, timedelta

from app.exceptions.base import QuotaExceededError
from app.quota.models import QuotaConfig, QuotaReservation, ReservationResult
from app.quota.repository import QuotaRepository
from app.usage.repository import UsageRepository

logger = logging.getLogger(__name__)


class QuotaService:
    def __init__(
        self,
        usage_repository: UsageRepository,
        quota_repository: QuotaRepository,
        config: QuotaConfig,
    ) -> None:
        self._usage_repo = usage_repository
        self._quota_repo = quota_repository
        self._config = config

    async def reserve(
        self,
        api_key_hash: str,
        max_tokens: int | None = None,
        prompt_tokens: int = 0,
    ) -> QuotaReservation | None:
        if not self._config.enabled:
            return None

        now = datetime.now(UTC)
        today = now.strftime("%Y-%m-%d")
        completion_tokens = max_tokens or self._config.default_reserve_tokens
        reserve_amount = prompt_tokens + completion_tokens

        reservation_id = uuid.uuid4().hex

        result = await self._quota_repo.create_reservation(
            reservation_id=reservation_id,
            api_key_hash=api_key_hash,
            usage_date=today,
            reserved_tokens=reserve_amount,
            daily_limit=self._config.daily_token_limit,
            monthly_limit=self._config.monthly_token_limit,
            reservation_ttl_seconds=self._config.reservation_ttl_seconds,
        )

        if result is ReservationResult.DAILY_LIMIT:
            await self._raise_daily_limit(api_key_hash, today, reserve_amount, now)
        if result is ReservationResult.MONTHLY_LIMIT:
            await self._raise_monthly_limit(api_key_hash, reserve_amount, now)
        if result is not ReservationResult.CREATED:
            raise RuntimeError(f"Unsupported quota reservation result: {result}")

        return QuotaReservation(
            reservation_id=reservation_id,
            api_key_hash=api_key_hash,
            reserved_tokens=reserve_amount,
            usage_date=today,
        )

    async def settle(self, reservation_id: str) -> None:
        if not self._config.enabled:
            return
        await self._quota_repo.settle_reservation(reservation_id)

    async def extend(self, reservation_id: str, additional_tokens: int) -> None:
        """Atomically extend an active reservation for a later model prompt."""
        if not self._config.enabled or additional_tokens <= 0:
            return
        result = await self._quota_repo.extend_reservation(
            reservation_id=reservation_id,
            additional_tokens=additional_tokens,
            daily_limit=self._config.daily_token_limit,
            monthly_limit=self._config.monthly_token_limit,
        )
        now = datetime.now(UTC)
        if result is ReservationResult.DAILY_LIMIT:
            raise QuotaExceededError(
                "Daily token quota exceeded.",
                retry_after=self._seconds_until_next_day(now),
            )
        if result is ReservationResult.MONTHLY_LIMIT:
            raise QuotaExceededError(
                "Monthly token quota exceeded.",
                retry_after=self._seconds_until_next_month(now),
            )
        if result is ReservationResult.NOT_FOUND:
            raise QuotaExceededError("Quota reservation is no longer active.")

    async def release(self, reservation_id: str) -> None:
        if not self._config.enabled:
            return
        await self._quota_repo.release_reservation(reservation_id)

    @property
    def reservation_renewal_seconds(self) -> int:
        return self._config.reservation_renewal_seconds

    async def renew(self, reservation_id: str) -> bool:
        if not self._config.enabled:
            return False
        return await self._quota_repo.renew_reservation(
            reservation_id, self._config.reservation_ttl_seconds
        )

    async def cleanup_expired(self) -> int:
        return await self._quota_repo.cleanup_expired()

    async def _raise_daily_limit(
        self,
        api_key_hash: str,
        usage_date: str,
        reserve_amount: int,
        now: datetime,
    ) -> None:
        daily_limit = self._config.daily_token_limit
        assert daily_limit is not None
        daily_used = await self._usage_repo.get_total_tokens_for_key(
            api_key_hash, usage_date
        )
        daily_reserved = await self._quota_repo.get_reserved_tokens_for_key(
            api_key_hash, usage_date
        )
        logger.warning(
            "quota_exceeded api_key_hash=%s daily_used=%d "
            "daily_reserved=%d reserve=%d limit=%d",
            api_key_hash[:8],
            daily_used,
            daily_reserved,
            reserve_amount,
            daily_limit,
        )
        raise QuotaExceededError(
            "Daily token quota exceeded.",
            retry_after=self._seconds_until_next_day(now),
        )

    async def _raise_monthly_limit(
        self,
        api_key_hash: str,
        reserve_amount: int,
        now: datetime,
    ) -> None:
        monthly_limit = self._config.monthly_token_limit
        assert monthly_limit is not None
        year_month = now.strftime("%Y-%m")
        monthly_aggs = await self._usage_repo.get_monthly_usage(
            api_key_hash, year_month
        )
        monthly_used = sum(aggregation.total_tokens for aggregation in monthly_aggs)
        monthly_reserved = await self._quota_repo.get_monthly_reserved_tokens_for_key(
            api_key_hash, year_month
        )
        logger.warning(
            "quota_exceeded api_key_hash=%s monthly_used=%d "
            "monthly_reserved=%d reserve=%d limit=%d",
            api_key_hash[:8],
            monthly_used,
            monthly_reserved,
            reserve_amount,
            monthly_limit,
        )
        raise QuotaExceededError(
            "Monthly token quota exceeded.",
            retry_after=self._seconds_until_next_month(now),
        )

    @staticmethod
    def _seconds_until_next_day(now: datetime) -> int:
        tomorrow = now.date() + timedelta(days=1)
        next_day = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=UTC)
        return int((next_day - now).total_seconds())

    @staticmethod
    def _seconds_until_next_month(now: datetime) -> int:
        if now.month == 12:
            next_month = datetime(now.year + 1, 1, 1, tzinfo=UTC)
        else:
            next_month = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
        return int((next_month - now).total_seconds())
