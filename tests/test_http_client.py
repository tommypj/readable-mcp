"""Tests for the resilient HTTP client (retries, backoff, Retry-After, timeouts)."""

from __future__ import annotations

import httpx
import pytest
import respx

from readable_mcp.config import Settings
from readable_mcp.http_client import FetchError, HttpClient
from readable_mcp.models import ErrorCode
from readable_mcp.rate_limiter import TokenBucket

URL = "https://upstream.test/page"


class RecordingSleep:
    """An awaitable that records requested delays instead of actually sleeping."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)


@pytest.fixture
def settings() -> Settings:
    return Settings(max_retries=3, retry_base_delay=0.01, total_timeout=2.0)


def _client(settings: Settings, sleeper: RecordingSleep) -> HttpClient:
    limiter = TokenBucket(rate=1000, burst=50)
    return HttpClient(settings, limiter, sleep=sleeper)


@respx.mock
async def test_retries_on_429_then_succeeds(settings):
    sleeper = RecordingSleep()
    respx.get(URL).mock(side_effect=[httpx.Response(429), httpx.Response(200, text="ok")])
    client = _client(settings, sleeper)
    result = await client.fetch(URL)
    assert result.status_code == 200
    assert result.text == "ok"
    assert len(sleeper.calls) == 1


@respx.mock
async def test_retries_on_503_then_succeeds(settings):
    sleeper = RecordingSleep()
    respx.get(URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, text="recovered")])
    client = _client(settings, sleeper)
    result = await client.fetch(URL)
    assert result.text == "recovered"


@respx.mock
async def test_honors_retry_after_header(settings):
    sleeper = RecordingSleep()
    respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, text="ok"),
        ]
    )
    client = _client(settings, sleeper)
    await client.fetch(URL)
    assert sleeper.calls == [2.0]


@respx.mock
async def test_gives_up_after_max_attempts(settings):
    sleeper = RecordingSleep()
    respx.get(URL).mock(side_effect=[httpx.Response(503)] * 3)
    client = _client(settings, sleeper)
    with pytest.raises(FetchError) as exc:
        await client.fetch(URL)
    assert exc.value.code is ErrorCode.HTTP_ERROR
    assert exc.value.retryable is True
    # 3 attempts -> 2 backoff sleeps between them.
    assert len(sleeper.calls) == 2


@respx.mock
async def test_does_not_retry_on_404(settings):
    sleeper = RecordingSleep()
    respx.get(URL).mock(return_value=httpx.Response(404))
    client = _client(settings, sleeper)
    with pytest.raises(FetchError) as exc:
        await client.fetch(URL)
    assert exc.value.code is ErrorCode.HTTP_ERROR
    assert exc.value.retryable is False
    assert sleeper.calls == []  # no retry


@respx.mock
async def test_timeout_surfaces_as_fetch_timeout(settings):
    sleeper = RecordingSleep()
    respx.get(URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    client = _client(settings, sleeper)
    with pytest.raises(FetchError) as exc:
        await client.fetch(URL)
    assert exc.value.code is ErrorCode.FETCH_TIMEOUT
    assert exc.value.retryable is True
