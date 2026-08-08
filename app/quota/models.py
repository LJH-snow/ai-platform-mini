from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class ReservationResult(StrEnum):
    CREATED = "created"
    DAILY_LIMIT = "daily_limit"
    MONTHLY_LIMIT = "monthly_limit"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class QuotaConfig:
    daily_token_limit: int | None = None
    monthly_token_limit: int | None = None
    default_reserve_tokens: int = 512
    reservation_ttl_seconds: int = 600
    reservation_renewal_seconds: int = 60
    # key = per-API-key limits (legacy, byte-identical); workspace =
    # workspace-bound keys share the workspace aggregate.  Legacy keys
    # (workspace_id NULL) are always key-scoped.
    quota_scope: Literal["key", "workspace"] = "key"

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
    workspace_id: str | None = None


@dataclass(frozen=True)
class WorkspaceQuota:
    """Per-workspace quota overrides; None inherits the global default."""

    workspace_id: str
    daily_token_limit: int | None = None
    monthly_token_limit: int | None = None
