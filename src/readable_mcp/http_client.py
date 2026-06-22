"""Resilient async HTTP client: rate-limited, retried, time-bounded.

This wraps ``httpx.AsyncClient`` with the three things a production fetcher needs and
a naive one omits:

* a shared **token-bucket** gate on every outbound request,
* **exponential backoff with jitter** on transient failures (timeouts, connection
  errors, HTTP 429 and 5xx), honoring ``Retry-After`` on 429, and
* explicit **connect/read/total timeouts** so a request can never hang forever.

4xx responses other than 429 are caller errors and are returned immediately without
retrying. All failures surface as a typed :class:`FetchError`, never a raw exception.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

from .config import Settings
from .models import ErrorCode
from .rate_limiter import TokenBucket

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class FetchError(Exception):
    """A typed transport/HTTP failure carrying an :class:`ErrorCode`."""

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(slots=True)
class FetchResult:
    """The outcome of a successful fetch."""

    final_url: str
    text: str
    status_code: int
    content_type: str


def _backoff_delay(attempt: int, base: float) -> float:
    """Exponential backoff (``base * 2**attempt``) plus full jitter in ``[0, base]``."""
    return base * (2**attempt) + random.uniform(0.0, base)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds or HTTP-date) into seconds."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    import datetime as _dt

    now = _dt.datetime.now(_dt.UTC)
    delta = (when - now).total_seconds()
    return max(0.0, delta)


class HttpClient:
    """Async HTTP client with rate limiting, retries, and hard timeouts."""

    def __init__(
        self,
        settings: Settings,
        limiter: TokenBucket,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._limiter = limiter
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.connect_timeout,
                read=settings.read_timeout,
                write=settings.read_timeout,
                pool=settings.connect_timeout,
            ),
            follow_redirects=True,
            headers={"User-Agent": settings.user_agent},
        )

    async def aclose(self) -> None:
        """Close the underlying client if we created it."""
        if self._owns_client:
            await self._client.aclose()

    async def fetch(self, url: str) -> FetchResult:
        """Fetch ``url`` with rate limiting, retries, and timeouts.

        Returns a :class:`FetchResult` on success. Raises :class:`FetchError` with a
        typed code when the request ultimately fails.
        """
        attempts = max(1, self._settings.max_retries)
        last_error: FetchError | None = None

        for attempt in range(attempts):
            is_last = attempt == attempts - 1
            await self._limiter.acquire()
            try:
                response = await asyncio.wait_for(
                    self._client.get(url),
                    timeout=self._settings.total_timeout,
                )
            except (TimeoutError, httpx.TimeoutException):
                last_error = FetchError(
                    ErrorCode.FETCH_TIMEOUT,
                    f"Request to {url} timed out after {self._settings.total_timeout}s.",
                    retryable=True,
                )
            except httpx.HTTPError as exc:
                last_error = FetchError(
                    ErrorCode.HTTP_ERROR,
                    f"Transport error fetching {url}: {exc}",
                    retryable=True,
                )
            else:
                status = response.status_code
                if status in _RETRYABLE_STATUS:
                    code = ErrorCode.RATE_LIMITED if status == 429 else ErrorCode.HTTP_ERROR
                    last_error = FetchError(
                        code,
                        f"Upstream returned HTTP {status} for {url}.",
                        retryable=True,
                    )
                    if not is_last:
                        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                        delay = (
                            retry_after
                            if retry_after is not None
                            else _backoff_delay(attempt, self._settings.retry_base_delay)
                        )
                        await self._sleep(delay)
                        continue
                    raise last_error
                if status >= 400:
                    # 4xx other than 429: caller error, do not retry.
                    raise FetchError(
                        ErrorCode.HTTP_ERROR,
                        f"Upstream returned HTTP {status} for {url}.",
                        retryable=False,
                    )
                return FetchResult(
                    final_url=str(response.url),
                    text=response.text,
                    status_code=status,
                    content_type=response.headers.get("Content-Type", ""),
                )

            # We reach here only on a retryable transport/timeout error.
            if is_last:
                raise last_error
            await self._sleep(_backoff_delay(attempt, self._settings.retry_base_delay))

        # Defensive: the loop always returns or raises, but satisfy the type checker.
        raise last_error or FetchError(
            ErrorCode.HTTP_ERROR, f"Failed to fetch {url}.", retryable=True
        )
