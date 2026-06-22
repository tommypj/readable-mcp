"""Pydantic v2 I/O contracts for readable-mcp tools.

These models are returned directly from the MCP tools, so FastMCP turns them into
the structured output schema the calling LLM sees. Keep field names and docstrings
descriptive for that audience.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
    """Stable, machine-readable error codes returned to MCP clients."""

    INVALID_URL = "INVALID_URL"
    BLOCKED_HOST = "BLOCKED_HOST"
    FETCH_TIMEOUT = "FETCH_TIMEOUT"
    HTTP_ERROR = "HTTP_ERROR"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    TOO_MANY_URLS = "TOO_MANY_URLS"


class ErrorDetail(BaseModel):
    """A structured, typed error. Tools never raise raw exceptions to the client."""

    code: ErrorCode = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable explanation of what went wrong.")
    retryable: bool = Field(
        description="True if the caller can reasonably retry the same request later."
    )


class ExtractionResult(BaseModel):
    """Clean, extracted content for a single URL plus its metadata."""

    url: str = Field(description="The URL as requested by the caller.")
    final_url: str = Field(description="The URL actually fetched, after any redirects.")
    title: str | None = Field(default=None, description="Detected page/article title.")
    author: str | None = Field(default=None, description="Detected author, if any.")
    published: str | None = Field(
        default=None, description="Detected publication date (ISO-ish string), if any."
    )
    word_count: int = Field(description="Word count of the extracted content.")
    output_format: str = Field(description="One of 'markdown', 'text', or 'html'.")
    content: str = Field(description="The extracted main content in the requested format.")
    from_cache: bool = Field(description="True if this result was served from the cache.")
    fetched_at: str = Field(description="ISO-8601 UTC timestamp of when the page was fetched.")


class ExtractionError(BaseModel):
    """A failed extraction for a single URL, carrying a typed error."""

    url: str = Field(description="The URL that failed.")
    error: ErrorDetail = Field(description="The structured error describing the failure.")


class BatchResult(BaseModel):
    """Result of a batch extraction; partial success is first-class."""

    requested: int = Field(description="Number of URLs accepted for processing.")
    succeeded: int = Field(description="Count of URLs that extracted successfully.")
    failed: int = Field(description="Count of URLs that failed.")
    results: list[ExtractionResult | ExtractionError] = Field(
        description="Per-URL results, in request order; each is a success or a typed error."
    )


class ServerStats(BaseModel):
    """Lightweight operational snapshot of the running server."""

    version: str = Field(description="readable-mcp version.")
    uptime_seconds: float = Field(description="Seconds since the server process started.")
    requests_served: int = Field(description="Total extraction requests handled.")
    cache_hits: int = Field(description="Number of cache hits.")
    cache_misses: int = Field(description="Number of cache misses.")
    cache_hit_rate: float = Field(description="hits / (hits + misses), 0.0 when no lookups yet.")
    rate_limit_rps: float = Field(description="Configured outbound requests-per-second.")
    cache_ttl_seconds: int = Field(description="Configured cache TTL in seconds.")
