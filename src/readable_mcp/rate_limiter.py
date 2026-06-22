"""Async token-bucket rate limiter.

A single shared limiter throttles *all* outbound fetches, including the concurrent
fetches inside ``extract_batch``. Tokens refill continuously at ``rate`` per second up
to ``burst`` capacity; :meth:`acquire` blocks (cooperatively) until a token is free.
"""

from __future__ import annotations

import asyncio


class TokenBucket:
    """A cooperative, async-safe token-bucket limiter.

    Args:
        rate: Sustained refill rate in tokens per second (must be > 0).
        burst: Maximum number of tokens that can accumulate (the bucket size).
    """

    def __init__(self, rate: float, burst: int) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self._rate = float(rate)
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._lock = asyncio.Lock()
        self._updated = asyncio.get_event_loop().time()

    def _refill(self, now: float) -> None:
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._updated = now

    async def acquire(self, tokens: float = 1.0) -> None:
        """Block until ``tokens`` are available, then consume them.

        Safe to call from many coroutines concurrently; waiters are serialized
        through an internal lock so the bucket is never over-drawn.
        """
        while True:
            async with self._lock:
                now = asyncio.get_event_loop().time()
                self._refill(now)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit / self._rate
            # Sleep outside the lock so other coroutines can refill/observe.
            await asyncio.sleep(wait)

    @property
    def available_tokens(self) -> float:
        """Best-effort snapshot of currently available tokens (for introspection)."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._updated
        return min(self._capacity, self._tokens + max(0.0, elapsed) * self._rate)
