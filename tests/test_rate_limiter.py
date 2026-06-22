"""Tests for the async token-bucket rate limiter."""

from __future__ import annotations

import asyncio
import time

import pytest

from readable_mcp.rate_limiter import TokenBucket


async def test_burst_is_immediate():
    bucket = TokenBucket(rate=5, burst=5)
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    # All 5 initial tokens are available up front -> effectively no wait.
    assert time.monotonic() - start < 0.1


async def test_enforces_rate_after_burst():
    # burst=1 so each acquire past the first must wait ~1/rate seconds.
    bucket = TokenBucket(rate=10, burst=1)
    await bucket.acquire()  # consumes the initial token
    start = time.monotonic()
    await bucket.acquire()  # must wait ~0.1s for a refill
    elapsed = time.monotonic() - start
    assert 0.08 <= elapsed <= 0.3


async def test_concurrent_acquire_is_safe():
    bucket = TokenBucket(rate=50, burst=5)
    start = time.monotonic()
    await asyncio.gather(*(bucket.acquire() for _ in range(15)))
    elapsed = time.monotonic() - start
    # 5 free + 10 more at 50/s -> ~0.2s lower bound; never negative or deadlocked.
    assert elapsed >= 0.15


@pytest.mark.parametrize("bad", [(0, 5), (-1, 5)])
def test_rejects_non_positive_rate(bad):
    rate, burst = bad
    with pytest.raises(ValueError):
        TokenBucket(rate=rate, burst=burst)


def test_rejects_zero_burst():
    with pytest.raises(ValueError):
        TokenBucket(rate=5, burst=0)
