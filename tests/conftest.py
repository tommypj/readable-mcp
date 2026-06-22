"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from readable_mcp.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings() -> Settings:
    """Fast, deterministic settings for tests (tiny delays, small bucket)."""
    return Settings(
        rate_limit_rps=1000.0,
        rate_limit_burst=50,
        max_retries=3,
        retry_base_delay=0.01,
        connect_timeout=1.0,
        read_timeout=2.0,
        total_timeout=3.0,
        cache_ttl=60,
        cache_maxsize=16,
        max_batch_urls=10,
    )


@pytest.fixture
def article_html() -> str:
    """A realistic article fixture."""
    return (FIXTURES / "article.html").read_text(encoding="utf-8")


@pytest.fixture
def blog_html() -> str:
    """A second, simpler article fixture."""
    return (FIXTURES / "blog.html").read_text(encoding="utf-8")
