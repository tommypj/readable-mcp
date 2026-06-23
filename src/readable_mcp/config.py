"""Typed configuration for readable-mcp.

All settings can be overridden via environment variables prefixed with
``READABLE_MCP_`` (e.g. ``READABLE_MCP_RATE_LIMIT_RPS=10``) or via a ``.env`` file.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, sourced from environment / ``.env``.

    Defaults are production-sane: a polite outbound rate, bounded retries,
    explicit timeouts, and a short-lived response cache.
    """

    model_config = SettingsConfigDict(
        env_prefix="READABLE_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Rate limiting (token bucket)
    rate_limit_rps: float = 5.0
    rate_limit_burst: int = 10

    # Retries with backoff
    max_retries: int = 3
    retry_base_delay: float = 0.5

    # Timeouts (seconds)
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    total_timeout: float = 20.0

    # Response cache
    cache_ttl: int = 900
    cache_maxsize: int = 512

    # Batch limits
    max_batch_urls: int = 10

    # Outbound identity
    user_agent: str = "readable-mcp/0.1 (+https://github.com/tommypj/readable-mcp)"

    # Logging
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance."""
    return Settings()
