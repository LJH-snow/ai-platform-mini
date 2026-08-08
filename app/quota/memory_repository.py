import logging
from datetime import UTC, datetime, timedelta

from app.quota.models import ReservationResult, WorkspaceQuota
from app.usage.repository import UsageRepository

logger = logging.getLogger(__name__)


class InMemoryQuotaRepository:
    def __init__(self, usage_repository: UsageRepository) -> None:
        self._workspace_quotas: dict[str, WorkspaceQuota] = {}
        self._usage_repo = usage_repository
        self._reservations: dict[str, dict] = {}

    async def create_reservation(
        self,
        reservation_id: str,
        api_key_hash: str,
        usage_date: str,
        reserved_tokens: int,
        daily_limit: int | None,
        monthly_limit: int | None,
        reservation_ttl_seconds: int,
        *,
        workspace_id: str | None = None,
        lock_key: str = "",
    ) -> ReservationResult:
        del lock_key
        self._purge_expired()

        if daily_limit is not None:
            if workspace_id is not None:
                daily_used = await self._usage_repo.get_total_tokens_for_workspace(
                    workspace_id, usage_date
                )
                daily_reserved = sum(
                    reservation["reserved_tokens"]
                    for reservation in self._reservations.values()
                    if reservation["workspace_id"] == workspace_id
                    and reservation["usage_date"] == usage_date
                    and not reservation["settled"]
                    and reservation["expires_at"] > datetime.now(UTC)
                )
            else:
                daily_used = await self._usage_repo.get_total_tokens_for_key(
                    api_key_hash, usage_date
                )
                daily_reserved = await self.get_reserved_tokens_for_key(
                    api_key_hash, usage_date
                )
            if daily_used + daily_reserved + reserved_tokens > daily_limit:
                return ReservationResult.DAILY_LIMIT

        if monthly_limit is not None:
            year_month = usage_date[:7]
            if workspace_id is not None:
                monthly_aggs = await self._usage_repo.get_monthly_usage_for_workspace(
                    workspace_id, year_month
                )
                monthly_reserved = sum(
                    reservation["reserved_tokens"]
                    for reservation in self._reservations.values()
                    if reservation["workspace_id"] == workspace_id
                    and reservation["usage_date"].startswith(year_month)
                    and not reservation["settled"]
                    and reservation["expires_at"] > datetime.now(UTC)
                )
            else:
                monthly_aggs = await self._usage_repo.get_monthly_usage(
                    api_key_hash, year_month
                )
                monthly_reserved = await self.get_monthly_reserved_tokens_for_key(
                    api_key_hash, year_month
                )
            monthly_used = sum(a.total_tokens for a in monthly_aggs)
            if monthly_used + monthly_reserved + reserved_tokens > monthly_limit:
                return ReservationResult.MONTHLY_LIMIT

        now = datetime.now(UTC)
        self._reservations[reservation_id] = {
            "api_key_hash": api_key_hash,
            "workspace_id": workspace_id,
            "usage_date": usage_date,
            "reserved_tokens": reserved_tokens,
            "settled": False,
            "expires_at": now + timedelta(seconds=reservation_ttl_seconds),
        }
        return ReservationResult.CREATED

    async def settle_reservation(self, reservation_id: str) -> None:
        self._reservations.pop(reservation_id, None)

    async def extend_reservation(
        self,
        reservation_id: str,
        additional_tokens: int,
        daily_limit: int | None,
        monthly_limit: int | None,
        *,
        workspace_id: str | None = None,
    ) -> ReservationResult:
        self._purge_expired()
        entry = self._reservations.get(reservation_id)
        if entry is None or entry["settled"]:
            return ReservationResult.NOT_FOUND

        api_key_hash = entry["api_key_hash"]
        usage_date = entry["usage_date"]
        current_reserved = entry["reserved_tokens"]
        if daily_limit is not None:
            daily_used = await self._usage_repo.get_total_tokens_for_key(
                api_key_hash, usage_date
            )
            daily_reserved = await self.get_reserved_tokens_for_key(
                api_key_hash, usage_date
            )
            if daily_used + daily_reserved + additional_tokens > daily_limit:
                return ReservationResult.DAILY_LIMIT

        if monthly_limit is not None:
            monthly_aggs = await self._usage_repo.get_monthly_usage(
                api_key_hash, usage_date[:7]
            )
            monthly_used = sum(a.total_tokens for a in monthly_aggs)
            monthly_reserved = await self.get_monthly_reserved_tokens_for_key(
                api_key_hash, usage_date[:7]
            )
            if monthly_used + monthly_reserved + additional_tokens > monthly_limit:
                return ReservationResult.MONTHLY_LIMIT

        entry["reserved_tokens"] = current_reserved + additional_tokens
        return ReservationResult.CREATED

    async def release_reservation(self, reservation_id: str) -> None:
        self._reservations.pop(reservation_id, None)

    async def renew_reservation(
        self, reservation_id: str, reservation_ttl_seconds: int
    ) -> bool:
        entry = self._reservations.get(reservation_id)
        now = datetime.now(UTC)
        if entry is None or entry["settled"] or entry["expires_at"] <= now:
            return False

        entry["expires_at"] = now + timedelta(seconds=reservation_ttl_seconds)
        return True

    async def get_reserved_tokens_for_key(
        self, api_key_hash: str, usage_date: str
    ) -> int:
        now = datetime.now(UTC)
        total = 0
        for entry in self._reservations.values():
            if (
                entry["api_key_hash"] == api_key_hash
                and entry["usage_date"] == usage_date
                and not entry["settled"]
                and entry["expires_at"] > now
            ):
                total += entry["reserved_tokens"]
        return total

    async def get_monthly_reserved_tokens_for_key(
        self, api_key_hash: str, year_month: str
    ) -> int:
        now = datetime.now(UTC)
        total = 0
        for entry in self._reservations.values():
            if (
                entry["api_key_hash"] == api_key_hash
                and entry["usage_date"][:7] == year_month
                and not entry["settled"]
                and entry["expires_at"] > now
            ):
                total += entry["reserved_tokens"]
        return total

    async def get_workspace_quota(self, workspace_id: str) -> WorkspaceQuota | None:
        quota = self._workspace_quotas.get(workspace_id)
        return quota

    async def set_workspace_quota(
        self, workspace_id: str, *, daily: int | None, monthly: int | None
    ) -> WorkspaceQuota:
        quota = WorkspaceQuota(
            workspace_id=workspace_id,
            daily_token_limit=daily,
            monthly_token_limit=monthly,
        )
        self._workspace_quotas[workspace_id] = quota
        return quota

    async def cleanup_expired(self) -> int:
        before = len(self._reservations)
        self._purge_expired()
        return before - len(self._reservations)

    def _purge_expired(self) -> None:
        now = datetime.now(UTC)
        expired_ids = [
            rid
            for rid, entry in self._reservations.items()
            if entry["settled"] or entry["expires_at"] <= now
        ]
        for rid in expired_ids:
            del self._reservations[rid]
