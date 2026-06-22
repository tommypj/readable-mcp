"""Tests for the async-safe TTL response cache."""

from __future__ import annotations

import asyncio

from readable_mcp.cache import ResponseCache


async def test_miss_then_set_then_hit():
    cache = ResponseCache(maxsize=8, ttl=60)
    assert await cache.get("https://a.test", "markdown") is None
    assert cache.misses == 1
    assert cache.hits == 0

    await cache.set("https://a.test", "markdown", {"content": "hi"})
    value = await cache.get("https://a.test", "markdown")
    assert value == {"content": "hi"}
    assert cache.hits == 1


async def test_format_is_part_of_key():
    cache = ResponseCache(maxsize=8, ttl=60)
    await cache.set("https://a.test", "markdown", "MD")
    assert await cache.get("https://a.test", "markdown") == "MD"
    # Same URL, different format -> miss.
    assert await cache.get("https://a.test", "text") is None


async def test_ttl_expiry():
    cache = ResponseCache(maxsize=8, ttl=1)
    await cache.set("https://a.test", "markdown", "v")
    assert await cache.get("https://a.test", "markdown") == "v"
    await asyncio.sleep(1.1)
    assert await cache.get("https://a.test", "markdown") is None


async def test_hit_rate_counter():
    cache = ResponseCache(maxsize=8, ttl=60)
    assert cache.hit_rate == 0.0
    await cache.set("u", "markdown", 1)
    await cache.get("u", "markdown")  # hit
    await cache.get("missing", "markdown")  # miss
    assert cache.hits == 1
    assert cache.misses == 1
    assert cache.hit_rate == 0.5
