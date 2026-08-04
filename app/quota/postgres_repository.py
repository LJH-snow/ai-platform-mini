import hashlib
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.quota.models import ReservationResult
from app.usage.repository import UsageRepository

logger = logging.getLogger(__name__)


def _advisory_lock_int(api_key_hash: str) -> int:
    digest = hashlib.sha256(api_key_hash.encode()).hexdigest()[:8]
    return int(digest, 16)


class PostgresQuotaRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        usage_repository: UsageRepository,
    ) -> None:
        self._session_factory = session_factory
        self._usage_repo = usage_repository

    async def create_reservation(
        self,
        reservation_id: str,
        api_key_hash: str,
        usage_date: str,
        reserved_tokens: int,
        daily_limit: int | None,
        monthly_limit: int | None,
        reservation_ttl_seconds: int,
    ) -> ReservationResult:
        lock_key = _advisory_lock_int(api_key_hash)

        async with self._session_factory() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": lock_key}
            )

            if daily_limit is not None:
                # Usage reads happen in a separate session; advisory lock
                # prevents concurrent reservations but usage committed
                # after this read is invisible — accepted tradeoff since
                # the lock already prevents oversubscription.
                daily_used = await self._usage_repo.get_total_tokens_for_key(
                    api_key_hash, usage_date
                )
                daily_reserved = await self._get_daily_reserved(
                    session, api_key_hash, usage_date
                )
                if daily_used + daily_reserved + reserved_tokens > daily_limit:
                    return ReservationResult.DAILY_LIMIT

            if monthly_limit is not None:
                year_month = usage_date[:7]
                monthly_aggs = await self._usage_repo.get_monthly_usage(
                    api_key_hash, year_month
                )
                monthly_used = sum(a.total_tokens for a in monthly_aggs)
                monthly_reserved = await self._get_monthly_reserved(
                    session, api_key_hash, year_month
                )
                if monthly_used + monthly_reserved + reserved_tokens > monthly_limit:
                    return ReservationResult.MONTHLY_LIMIT

            expires_at = datetime.now(UTC) + timedelta(seconds=reservation_ttl_seconds)
            await session.execute(
                text(
                    "INSERT INTO quota_reservations "
                    "(id, api_key_hash, usage_date, reserved_tokens, "
                    "settled, expires_at) "
                    "VALUES (:id, :hash, :date, :tokens, false, :expires)"
                ),
                {
                    "id": reservation_id,
                    "hash": api_key_hash,
                    "date": date.fromisoformat(usage_date),
                    "tokens": reserved_tokens,
                    "expires": expires_at,
                },
            )
            await session.commit()
            return ReservationResult.CREATED

    async def settle_reservation(self, reservation_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                text("DELETE FROM quota_reservations WHERE id = :id"),
                {"id": reservation_id},
            )
            await session.commit()

    async def extend_reservation(
        self,
        reservation_id: str,
        additional_tokens: int,
        daily_limit: int | None,
        monthly_limit: int | None,
    ) -> ReservationResult:
        async with self._session_factory() as session:
            reservation_key = await session.execute(
                text("SELECT api_key_hash FROM quota_reservations WHERE id = :id"),
                {"id": reservation_id},
            )
            reservation_key_value = reservation_key.scalar_one_or_none()
            if reservation_key_value is None:
                return ReservationResult.NOT_FOUND
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock)"),
                {"lock": _advisory_lock_int(str(reservation_key_value))},
            )
            reservation_result = await session.execute(
                text(
                    "SELECT api_key_hash, usage_date, reserved_tokens "
                    "FROM quota_reservations "
                    "WHERE id = :id AND settled = false AND expires_at > now() "
                    "FOR UPDATE"
                ),
                {"id": reservation_id},
            )
            reservation = reservation_result.mappings().first()
            if reservation is None:
                return ReservationResult.NOT_FOUND

            api_key_hash = str(reservation["api_key_hash"])
            usage_date = reservation["usage_date"].isoformat()
            if daily_limit is not None:
                daily_used = await self._usage_repo.get_total_tokens_for_key(
                    api_key_hash, usage_date
                )
                daily_reserved = await self._get_daily_reserved(
                    session, api_key_hash, usage_date
                )
                if daily_used + daily_reserved + additional_tokens > daily_limit:
                    return ReservationResult.DAILY_LIMIT

            if monthly_limit is not None:
                year_month = usage_date[:7]
                monthly_aggs = await self._usage_repo.get_monthly_usage(
                    api_key_hash, year_month
                )
                monthly_used = sum(a.total_tokens for a in monthly_aggs)
                monthly_reserved = await self._get_monthly_reserved(
                    session, api_key_hash, year_month
                )
                if monthly_used + monthly_reserved + additional_tokens > monthly_limit:
                    return ReservationResult.MONTHLY_LIMIT

            await session.execute(
                text(
                    "UPDATE quota_reservations "
                    "SET reserved_tokens = reserved_tokens + :additional "
                    "WHERE id = :id"
                ),
                {"id": reservation_id, "additional": additional_tokens},
            )
            await session.commit()
            return ReservationResult.CREATED

    async def release_reservation(self, reservation_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                text("DELETE FROM quota_reservations WHERE id = :id"),
                {"id": reservation_id},
            )
            await session.commit()

    async def renew_reservation(
        self, reservation_id: str, reservation_ttl_seconds: int
    ) -> bool:
        expires_at = datetime.now(UTC) + timedelta(seconds=reservation_ttl_seconds)
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "UPDATE quota_reservations "
                    "SET expires_at = :expires "
                    "WHERE id = :id AND settled = false AND expires_at > now() "
                    "RETURNING id"
                ),
                {"id": reservation_id, "expires": expires_at},
            )
            await session.commit()
            return result.scalar_one_or_none() is not None

    async def get_reserved_tokens_for_key(
        self, api_key_hash: str, usage_date: str
    ) -> int:
        async with self._session_factory() as session:
            return await self._get_daily_reserved(session, api_key_hash, usage_date)

    async def get_monthly_reserved_tokens_for_key(
        self, api_key_hash: str, year_month: str
    ) -> int:
        async with self._session_factory() as session:
            return await self._get_monthly_reserved(session, api_key_hash, year_month)

    async def cleanup_expired(self) -> int:
        async with self._session_factory() as session:
            row = await session.execute(
                text(
                    "WITH deleted AS ("
                    "DELETE FROM quota_reservations "
                    "WHERE settled = true OR expires_at < now() "
                    "RETURNING id"
                    ") SELECT count(*) FROM deleted"
                )
            )
            await session.commit()
            count_val = row.scalar()
            return int(count_val) if count_val is not None else 0

    @staticmethod
    async def _get_daily_reserved(
        session: AsyncSession, api_key_hash: str, usage_date: str
    ) -> int:
        row = await session.execute(
            text(
                "SELECT COALESCE(SUM(reserved_tokens), 0) "
                "FROM quota_reservations "
                "WHERE api_key_hash = :hash AND usage_date = :date "
                "AND settled = false AND expires_at > now()"
            ),
            {
                "hash": api_key_hash,
                "date": date.fromisoformat(usage_date),
            },
        )
        result = row.scalar()
        return int(result) if result is not None else 0

    @staticmethod
    async def _get_monthly_reserved(
        session: AsyncSession, api_key_hash: str, year_month: str
    ) -> int:
        year, month = year_month.split("-")
        start = f"{year}-{month}-01"
        if month == "12":
            end = f"{int(year) + 1}-01-01"
        else:
            end = f"{year}-{int(month) + 1:02d}-01"

        row = await session.execute(
            text(
                "SELECT COALESCE(SUM(reserved_tokens), 0) "
                "FROM quota_reservations "
                "WHERE api_key_hash = :hash "
                "AND usage_date >= :start AND usage_date < :end "
                "AND settled = false AND expires_at > now()"
            ),
            {
                "hash": api_key_hash,
                "start": date.fromisoformat(start),
                "end": date.fromisoformat(end),
            },
        )
        result = row.scalar()
        return int(result) if result is not None else 0
