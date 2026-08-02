import pytest

from app.auth.models import APIKey
from app.exceptions.base import RateLimitError
from app.ratelimit.memory import MemorySlidingWindowLimiter
from app.ratelimit.service import RateLimitService


def _make_service(limit: int) -> RateLimitService:
    limiter = MemorySlidingWindowLimiter(limit=limit, window_seconds=60)
    return RateLimitService(limiter=limiter)


def test_acquire_allows_under_limit() -> None:
    limiter = MemorySlidingWindowLimiter(limit=3, window_seconds=60)
    result = limiter.acquire("sk-test")
    assert result.allowed is True
    assert result.remaining == 2
    assert result.limit == 3


def test_acquire_remaining_decrements() -> None:
    limiter = MemorySlidingWindowLimiter(limit=3, window_seconds=60)
    limiter.acquire("sk-test")
    result = limiter.acquire("sk-test")
    assert result.remaining == 1


def test_acquire_last_slot_remaining_zero() -> None:
    limiter = MemorySlidingWindowLimiter(limit=2, window_seconds=60)
    limiter.acquire("sk-test")
    result = limiter.acquire("sk-test")
    assert result.allowed is True
    assert result.remaining == 0


def test_acquire_blocks_over_limit() -> None:
    limiter = MemorySlidingWindowLimiter(limit=2, window_seconds=60)
    limiter.acquire("sk-test")
    limiter.acquire("sk-test")
    result = limiter.acquire("sk-test")
    assert result.allowed is False
    assert result.remaining == 0


def test_acquire_key_isolation() -> None:
    limiter = MemorySlidingWindowLimiter(limit=1, window_seconds=60)
    limiter.acquire("sk-a")
    result_b = limiter.acquire("sk-b")
    assert result_b.allowed is True
    assert result_b.remaining == 0


def test_service_allows_under_limit() -> None:
    service = _make_service(3)
    key = APIKey(key="sk-test", name="test")

    result = service.check_and_record(key)
    assert result.remaining == 2
    assert result.limit == 3


def test_service_blocks_over_limit() -> None:
    service = _make_service(2)
    key = APIKey(key="sk-test", name="test")

    service.check_and_record(key)
    service.check_and_record(key)

    with pytest.raises(RateLimitError, match="Rate limit exceeded"):
        service.check_and_record(key)


def test_service_per_key_isolation() -> None:
    service = _make_service(1)
    key_a = APIKey(key="sk-a", name="alice")
    key_b = APIKey(key="sk-b", name="bob")

    service.check_and_record(key_a)

    result = service.check_and_record(key_b)
    assert result.remaining == 0


def test_acquire_returns_reset_after() -> None:
    limiter = MemorySlidingWindowLimiter(limit=5, window_seconds=60)
    result = limiter.acquire("sk-test")
    assert 1 <= result.reset_after <= 60
