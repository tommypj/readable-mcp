"""Async-safe TTL response cache.

Wraps a :class:`cachetools.TTLCache` with an :class:`asyncio.Lock` so concurrent
tool calls cannot corrupt it, and tracks hit/miss counters for ``get_stats``.

Keys are ``(normalized_url, output_format)`` tuples; values are arbitrary (we store
extracted payloads). The cache stores successful extractions only.
"""

from __future__ import annotations

import asyncio
from typing import Any

from cachetools import TTLCache


class ResponseCache:
    """A TTL cache guarded by an async lock, with hit/miss accounting."""

    def __init__(self, maxsize: int, ttl: int) -> None:
        self._cache: TTLCache[tuple[str, str], Any] = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    async def get(self, url: str, output_format: str) -> Any | None:
        """Return the cached value for the key, or ``None`` on miss/expiry."""
        key = (url, output_format)
        async with self._lock:
            try:
                value = self._cache[key]
            except KeyError:
                self._misses += 1
                return None
            self._hits += 1
            return value

    async def set(self, url: str, output_format: str, value: Any) -> None:
        """Store ``value`` under the ``(url, output_format)`` key."""
        async with self._lock:
            self._cache[(url, output_format)] = value

    @property
    def hits(self) -> int:
        """Total cache hits since startup."""
        return self._hits

    @property
    def misses(self) -> int:
        """Total cache misses since startup."""
        return self._misses

    @property
    def hit_rate(self) -> float:
        """hits / (hits + misses); 0.0 when there have been no lookups."""
        total = self._hits + self._misses
        return self._hits / total if total else 0.0
