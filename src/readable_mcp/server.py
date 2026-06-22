"""FastMCP server and tool definitions.

This module is intentionally thin: the tools validate input, delegate to the
:class:`ReadableServer` orchestrator, and always return a typed model — never a raw
exception. All real logic (validation, fetching, extraction, caching) lives in the
sibling modules.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from fastmcp import FastMCP

from . import __version__
from .cache import ResponseCache
from .config import Settings, get_settings
from .extractor import ExtractionFailed, extract_content
from .http_client import FetchError, HttpClient
from .logging_config import configure_logging, get_logger, new_request_id
from .models import (
    BatchResult,
    ErrorCode,
    ErrorDetail,
    ExtractionError,
    ExtractionResult,
    ServerStats,
)
from .rate_limiter import TokenBucket
from .validation import ValidationError, validate_url

_VALID_FORMATS = frozenset({"markdown", "text", "html"})

logger = get_logger()


class ReadableServer:
    """Holds shared state and implements the three tools' behavior.

    Constructed once per process. Tests may inject a custom :class:`HttpClient`
    (e.g. backed by ``respx``) to exercise the orchestration offline.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: HttpClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.limiter = TokenBucket(self.settings.rate_limit_rps, self.settings.rate_limit_burst)
        self.cache = ResponseCache(self.settings.cache_maxsize, self.settings.cache_ttl)
        self.http = http_client or HttpClient(self.settings, self.limiter)
        self._start = time.monotonic()
        self.requests_served = 0

    async def aclose(self) -> None:
        """Release the underlying HTTP client."""
        await self.http.aclose()

    async def extract_url(
        self, url: str, output_format: str = "markdown"
    ) -> ExtractionResult | ExtractionError:
        """Fetch and extract a single URL. Returns a result or a typed error."""
        self.requests_served += 1
        request_id = new_request_id()

        if output_format not in _VALID_FORMATS:
            return _error(
                url,
                ErrorCode.EXTRACTION_FAILED,
                f"Unsupported output_format {output_format!r}; "
                f"expected one of {sorted(_VALID_FORMATS)}.",
                retryable=False,
            )

        # 1. Validate + SSRF guard.
        try:
            normalized = validate_url(url)
        except ValidationError as exc:
            _log(request_id, "extract_url", url, outcome="rejected", code=exc.code.value)
            return _error(url, exc.code, exc.message, retryable=False)

        # 2. Cache lookup.
        cached = await self.cache.get(normalized, output_format)
        if cached is not None:
            _log(request_id, "extract_url", url, outcome="ok", cache="hit")
            return cached.model_copy(update={"from_cache": True})

        # 3. Fetch (rate-limited, retried, time-bounded).
        started = time.monotonic()
        try:
            fetched = await self.http.fetch(normalized)
        except FetchError as exc:
            _log(
                request_id,
                "extract_url",
                url,
                outcome="error",
                cache="miss",
                code=exc.code.value,
            )
            return _error(url, exc.code, exc.message, retryable=exc.retryable)

        # 4. Extract main content + metadata.
        try:
            doc = extract_content(fetched.text, fetched.final_url, output_format)
        except (ExtractionFailed, ValueError) as exc:
            _log(request_id, "extract_url", url, outcome="error", cache="miss")
            return _error(url, ErrorCode.EXTRACTION_FAILED, str(exc), retryable=False)

        result = ExtractionResult(
            url=url,
            final_url=fetched.final_url,
            title=doc.title,
            author=doc.author,
            published=doc.published,
            word_count=doc.word_count,
            output_format=output_format,
            content=doc.content,
            from_cache=False,
            fetched_at=datetime.now(UTC).isoformat(),
        )
        await self.cache.set(normalized, output_format, result)

        latency_ms = round((time.monotonic() - started) * 1000, 1)
        _log(
            request_id,
            "extract_url",
            url,
            outcome="ok",
            cache="miss",
            latency_ms=latency_ms,
            words=doc.word_count,
        )
        return result

    async def extract_batch(self, urls: list[str], output_format: str = "markdown") -> BatchResult:
        """Extract several URLs concurrently; partial success is first-class."""
        if not urls:
            return BatchResult(requested=0, succeeded=0, failed=0, results=[])

        max_urls = self.settings.max_batch_urls
        accepted = urls[:max_urls]
        excess = urls[max_urls:]

        concurrency = max(1, min(len(accepted), self.settings.rate_limit_burst))
        semaphore = asyncio.Semaphore(concurrency)

        async def _run(target: str) -> ExtractionResult | ExtractionError:
            async with semaphore:
                return await self.extract_url(target, output_format)

        results: list[ExtractionResult | ExtractionError] = list(
            await asyncio.gather(*(_run(u) for u in accepted))
        )

        for dropped in excess:
            results.append(
                _error(
                    dropped,
                    ErrorCode.TOO_MANY_URLS,
                    f"Batch limited to {max_urls} URLs per call; this URL was not processed.",
                    retryable=True,
                )
            )

        succeeded = sum(1 for r in results if isinstance(r, ExtractionResult))
        return BatchResult(
            requested=len(urls),
            succeeded=succeeded,
            failed=len(results) - succeeded,
            results=results,
        )

    def get_stats(self) -> ServerStats:
        """Return a lightweight operational snapshot."""
        return ServerStats(
            version=__version__,
            uptime_seconds=round(time.monotonic() - self._start, 3),
            requests_served=self.requests_served,
            cache_hits=self.cache.hits,
            cache_misses=self.cache.misses,
            cache_hit_rate=round(self.cache.hit_rate, 4),
            rate_limit_rps=self.settings.rate_limit_rps,
            cache_ttl_seconds=self.settings.cache_ttl,
        )


def _error(url: str, code: ErrorCode, message: str, *, retryable: bool) -> ExtractionError:
    """Build a typed :class:`ExtractionError`."""
    return ExtractionError(
        url=url, error=ErrorDetail(code=code, message=message, retryable=retryable)
    )


def _log(request_id: str, tool: str, url: str, **fields: object) -> None:
    """Emit one structured log line for a tool call (host only, never bodies)."""
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or "?"
    logger.info("tool_call", extra={"request_id": request_id, "tool": tool, "host": host, **fields})


# --------------------------------------------------------------------------------------
# FastMCP wiring
# --------------------------------------------------------------------------------------

mcp: FastMCP = FastMCP(
    name="readable-mcp",
    instructions=(
        "Fetch web pages and return clean, LLM-ready content (Markdown, text, or "
        "cleaned HTML) instead of raw HTML. Fetching is SSRF-safe, rate-limited, "
        "retried, and cached. Use extract_url for one page, extract_batch for many "
        "(max 10), and get_stats for server health."
    ),
)

_app: ReadableServer | None = None


def _get_app() -> ReadableServer:
    """Return the process-wide :class:`ReadableServer`, creating it on first use."""
    global _app
    if _app is None:
        _app = ReadableServer()
    return _app


@mcp.tool
async def extract_url(
    url: str, output_format: str = "markdown"
) -> ExtractionResult | ExtractionError:
    """Fetch a single URL and return its main content as clean Markdown, text, or HTML.

    Strips navigation, ads, and boilerplate, and returns the title, author, publish
    date (when detectable), canonical/final URL, word count, and a ``from_cache`` flag.

    Args:
        url: An ``http``/``https`` URL. Private, loopback, and link-local hosts are
            refused for security (SSRF protection).
        output_format: ``"markdown"`` (default), ``"text"``, or ``"html"`` (cleaned).

    Returns:
        An ``ExtractionResult`` on success, or an ``ExtractionError`` with a typed
        error code (``INVALID_URL``, ``BLOCKED_HOST``, ``FETCH_TIMEOUT``,
        ``HTTP_ERROR``, ``EXTRACTION_FAILED``, ``RATE_LIMITED``) on failure.
    """
    return await _get_app().extract_url(url, output_format)


@mcp.tool
async def extract_batch(urls: list[str], output_format: str = "markdown") -> BatchResult:
    """Extract up to 10 URLs concurrently; one bad URL never fails the batch.

    Each URL is fetched under the shared rate limiter and a concurrency cap. Results
    are returned per URL in request order, each marked success or a typed error.
    URLs beyond the limit of 10 are returned as ``TOO_MANY_URLS`` errors.

    Args:
        urls: The URLs to extract (max 10 processed per call).
        output_format: ``"markdown"`` (default), ``"text"``, or ``"html"`` (cleaned).

    Returns:
        A ``BatchResult`` with ``requested``/``succeeded``/``failed`` counts and the
        per-URL ``results`` list.
    """
    return await _get_app().extract_batch(urls, output_format)


@mcp.tool
async def get_stats() -> ServerStats:
    """Return server health: uptime, requests served, cache hit rate, and config.

    Contains no sensitive data — safe to expose to the model for self-diagnostics.
    """
    return _get_app().get_stats()


def main() -> None:
    """Console-script entry point: configure logging and run over stdio."""
    settings = get_settings()
    configure_logging(settings.log_level)
    _get_app()
    logger.info("server_start", extra={"version": __version__, "transport": "stdio"})
    mcp.run()


if __name__ == "__main__":
    main()
