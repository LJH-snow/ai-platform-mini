from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    limit: int
    reset_after: int


@runtime_checkable
class RateLimiter(Protocol):
    def acquire(self, key: str) -> RateLimitResult: ...
