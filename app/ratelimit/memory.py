import time
from collections import deque

from app.ratelimit.base import RateLimitResult


class MemorySlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._queues: dict[str, deque[float]] = {}

    def acquire(self, key: str) -> RateLimitResult:
        now = time.monotonic()
        cutoff = now - self._window_seconds
        queue = self._queues.get(key)
        if queue is None:
            queue = deque()
            self._queues[key] = queue

        while queue and queue[0] < cutoff:
            queue.popleft()

        reset_after = self._window_seconds
        if queue:
            reset_after = max(1, int(queue[0] + self._window_seconds - now))

        if len(queue) >= self._limit:
            return RateLimitResult(
                allowed=False,
                remaining=0,
                limit=self._limit,
                reset_after=reset_after,
            )

        queue.append(now)
        return RateLimitResult(
            allowed=True,
            remaining=self._limit - len(queue),
            limit=self._limit,
            reset_after=reset_after,
        )
