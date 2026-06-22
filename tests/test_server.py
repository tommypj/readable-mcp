"""Tests for the tool orchestration in ReadableServer (happy + error paths)."""

from __future__ import annotations

import pytest

from readable_mcp.http_client import FetchError, FetchResult
from readable_mcp.models import (
    BatchResult,
    ErrorCode,
    ExtractionError,
    ExtractionResult,
)
from readable_mcp.server import ReadableServer


class FakeHttp:
    """Stub HTTP client returning canned HTML (or raising) without any network."""

    def __init__(self, html: str = "", *, error: FetchError | None = None) -> None:
        self.html = html
        self.error = error
        self.calls = 0

    async def fetch(self, url: str) -> FetchResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return FetchResult(final_url=url, text=self.html, status_code=200, content_type="text/html")

    async def aclose(self) -> None:  # pragma: no cover - trivial
        pass


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Make hostname resolution deterministic + offline (always public)."""
    monkeypatch.setattr(
        "readable_mcp.validation._resolve_addresses", lambda host: ["93.184.216.34"]
    )


def make_server(settings, html="", error=None) -> ReadableServer:
    return ReadableServer(settings=settings, http_client=FakeHttp(html, error=error))


async def test_extract_url_happy_path(settings, article_html):
    server = make_server(settings, html=article_html)
    result = await server.extract_url("https://example.com/a", "markdown")
    assert isinstance(result, ExtractionResult)
    assert result.title == "The Case for Production-Grade Tooling"
    assert result.from_cache is False
    assert result.word_count > 0
    assert result.output_format == "markdown"


async def test_extract_url_bad_scheme_returns_structured_error(settings, article_html):
    server = make_server(settings, html=article_html)
    result = await server.extract_url("ftp://example.com/a")
    assert isinstance(result, ExtractionError)
    assert result.error.code is ErrorCode.INVALID_URL
    assert result.error.retryable is False


async def test_extract_url_blocked_host(settings, article_html):
    server = make_server(settings, html=article_html)
    result = await server.extract_url("http://127.0.0.1/admin")
    assert isinstance(result, ExtractionError)
    assert result.error.code is ErrorCode.BLOCKED_HOST


async def test_extract_url_maps_fetch_error(settings):
    err = FetchError(ErrorCode.FETCH_TIMEOUT, "slow", retryable=True)
    server = make_server(settings, error=err)
    result = await server.extract_url("https://example.com/a")
    assert isinstance(result, ExtractionError)
    assert result.error.code is ErrorCode.FETCH_TIMEOUT
    assert result.error.retryable is True


async def test_cache_hit_on_repeat(settings, article_html):
    server = make_server(settings, html=article_html)
    first = await server.extract_url("https://example.com/a", "markdown")
    second = await server.extract_url("https://example.com/a", "markdown")
    assert isinstance(first, ExtractionResult)
    assert isinstance(second, ExtractionResult)
    assert first.from_cache is False
    assert second.from_cache is True
    assert server.http.calls == 1  # second served from cache, no refetch


async def test_extract_batch_partial_success(settings, article_html):
    server = make_server(settings, html=article_html)
    result = await server.extract_batch(
        ["https://example.com/good", "http://127.0.0.1/bad"], "markdown"
    )
    assert isinstance(result, BatchResult)
    assert result.requested == 2
    assert result.succeeded == 1
    assert result.failed == 1
    kinds = {type(r) for r in result.results}
    assert ExtractionResult in kinds and ExtractionError in kinds


async def test_extract_batch_truncates_over_limit(settings, article_html):
    settings.max_batch_urls = 2
    server = make_server(settings, html=article_html)
    urls = [f"https://example.com/{i}" for i in range(4)]
    result = await server.extract_batch(urls)
    assert result.requested == 4
    too_many = [
        r
        for r in result.results
        if isinstance(r, ExtractionError) and r.error.code is ErrorCode.TOO_MANY_URLS
    ]
    assert len(too_many) == 2


async def test_extract_batch_empty(settings):
    server = make_server(settings)
    result = await server.extract_batch([])
    assert result.requested == 0
    assert result.results == []


async def test_get_stats_is_coherent(settings, article_html):
    server = make_server(settings, html=article_html)
    await server.extract_url("https://example.com/a")
    await server.extract_url("https://example.com/a")  # cache hit
    stats = server.get_stats()
    assert stats.requests_served == 2
    assert stats.cache_hits == 1
    assert stats.cache_misses == 1
    assert stats.cache_hit_rate == 0.5
    assert stats.version
    assert stats.uptime_seconds >= 0
