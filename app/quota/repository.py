from typing import Protocol, runtime_checkable

from app.quota.models import ReservationResult


@runtime_checkable
class QuotaRepository(Protocol):
    async def create_reservation(
        self,
        reservation_id: str,
        api_key_hash: str,
        usage_date: str,
        reserved_tokens: int,
        daily_limit: int | None,
        monthly_limit: int | None,
        reservation_ttl_seconds: int,
    ) -> ReservationResult: ...

    async def settle_reservation(self, reservation_id: str) -> None: ...

    async def extend_reservation(
        self,
        reservation_id: str,
        additional_tokens: int,
        daily_limit: int | None,
        monthly_limit: int | None,
    ) -> ReservationResult: ...

    async def release_reservation(self, reservation_id: str) -> None: ...

    async def renew_reservation(
        self, reservation_id: str, reservation_ttl_seconds: int
    ) -> bool: ...

    async def get_reserved_tokens_for_key(
        self, api_key_hash: str, usage_date: str
    ) -> int: ...

    async def get_monthly_reserved_tokens_for_key(
        self, api_key_hash: str, year_month: str
    ) -> int: ...

    async def cleanup_expired(self) -> int: ...
