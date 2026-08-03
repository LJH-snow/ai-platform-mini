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
        is_new = queue is None
        if is_new:
            queue = deque()
            self._queues[key] = queue

        while queue and queue[0] < cutoff:
            queue.popleft()

        # Garbage-collect queues that emptied naturally (all events expired
        # beyond the window). The rate limit has fully reset, so return a
        # fresh allowance without re-registering the key.
        if not is_new and not queue:
            del self._queues[key]
            return RateLimitResult(
                allowed=True,
                remaining=self._limit,
                limit=self._limit,
                reset_after=self._window_seconds,
            )

        # Narrow: after early-return above, queue is always a deque object.
        assert queue is not None

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
