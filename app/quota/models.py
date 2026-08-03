from dataclasses import dataclass
from enum import StrEnum


class ReservationResult(StrEnum):
    CREATED = "created"
    DAILY_LIMIT = "daily_limit"
    MONTHLY_LIMIT = "monthly_limit"


@dataclass(frozen=True)
class QuotaConfig:
    daily_token_limit: int | None = None
    monthly_token_limit: int | None = None
    default_reserve_tokens: int = 512
    reservation_ttl_seconds: int = 600
    reservation_renewal_seconds: int = 60

    @property
    def enabled(self) -> bool:
        return (
            self.daily_token_limit is not None or self.monthly_token_limit is not None
        )


@dataclass(frozen=True)
class QuotaReservation:
    reservation_id: str
    api_key_hash: str
    reserved_tokens: int
    usage_date: str
