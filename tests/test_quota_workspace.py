"""Workspace-scoped quota tests: shared limits, inheritance, legacy mixing."""

from __future__ import annotations

import pytest

from app.exceptions.base import QuotaExceededError
from app.quota.memory_repository import InMemoryQuotaRepository
from app.quota.models import QuotaConfig
from app.quota.service import QuotaService
from app.usage.memory_repository import InMemoryUsageRepository
from app.usage.models import UsageRecord


def _service(*, daily: int | None, monthly: int | None = None) -> QuotaService:
    usage_repo = InMemoryUsageRepository()
    return QuotaService(
        usage_repository=usage_repo,
        quota_repository=InMemoryQuotaRepository(usage_repo),
        config=QuotaConfig(
            daily_token_limit=daily,
            monthly_token_limit=monthly,
            quota_scope="workspace",
        ),
    )


async def _record_usage(service: QuotaService, ws: str, tokens: int) -> None:
    await service._usage_repo.record_usage(  # type: ignore[attr-defined]
        UsageRecord(
            request_id="r",
            model="m",
            total_tokens=tokens,
            api_key_hash="k",
            workspace_id=ws,
            usage_date="2026-08-08",
        )
    )


async def test_workspace_keys_share_the_daily_limit() -> None:
    service = _service(daily=100)

    first = await service.reserve("key-a", max_tokens=60, workspace_id="ws-1")
    assert first is not None

    # Second key of the same workspace is rejected against the shared limit.
    with pytest.raises(QuotaExceededError):
        await service.reserve("key-b", max_tokens=60, workspace_id="ws-1")

    # A different workspace is unaffected.
    other = await service.reserve("key-c", max_tokens=60, workspace_id="ws-2")
    assert other is not None
    assert other.workspace_id == "ws-2"


async def test_legacy_key_stays_key_scoped_in_workspace_mode() -> None:
    service = _service(daily=100)

    # Workspace key consumes workspace budget.
    await service.reserve("key-a", max_tokens=90, workspace_id="ws-1")

    # Legacy key (no workspace) is judged on its own key, not the workspace.
    legacy = await service.reserve("legacy-key", max_tokens=90)
    assert legacy is not None
    assert legacy.workspace_id is None


async def test_workspace_override_beats_global_default() -> None:
    service = _service(daily=1000)

    await service._quota_repo.set_workspace_quota(  # type: ignore[attr-defined]
        "ws-1", daily=50, monthly=None
    )

    # Workspace override (50) applies.
    with pytest.raises(QuotaExceededError):
        await service.reserve("key-a", max_tokens=60, workspace_id="ws-1")
    assert (
        await service.reserve("key-a", max_tokens=40, workspace_id="ws-1") is not None
    )
    # Global default (1000) still applies elsewhere.
    assert (
        await service.reserve("key-b", max_tokens=500, workspace_id="ws-2") is not None
    )


async def test_workspace_quota_clear_restores_inheritance() -> None:
    service = _service(daily=1000)
    await service._quota_repo.set_workspace_quota(  # type: ignore[attr-defined]
        "ws-1", daily=10, monthly=None
    )
    with pytest.raises(QuotaExceededError):
        await service.reserve("key-a", max_tokens=20, workspace_id="ws-1")

    await service._quota_repo.set_workspace_quota(  # type: ignore[attr-defined]
        "ws-1", daily=None, monthly=None
    )
    assert (
        await service.reserve("key-a", max_tokens=20, workspace_id="ws-1") is not None
    )


async def test_reservation_carries_workspace_id_in_key_mode_too() -> None:
    """The column is always written; only the judgement dimension switches."""
    service = QuotaService(
        usage_repository=InMemoryUsageRepository(),
        quota_repository=InMemoryQuotaRepository(InMemoryUsageRepository()),
        config=QuotaConfig(daily_token_limit=100, quota_scope="key"),
    )

    reservation = await service.reserve("key-a", max_tokens=60, workspace_id="ws-1")

    assert reservation is not None
    assert reservation.workspace_id == "ws-1"


async def test_monthly_workspace_limit() -> None:
    service = _service(daily=None, monthly=100)
    await _record_usage(service, "ws-1", 80)

    with pytest.raises(QuotaExceededError):
        await service.reserve("key-a", max_tokens=50, workspace_id="ws-1")
    assert (
        await service.reserve("key-a", max_tokens=10, workspace_id="ws-1") is not None
    )
