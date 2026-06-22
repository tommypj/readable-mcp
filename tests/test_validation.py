"""Tests for URL validation and the SSRF guard."""

from __future__ import annotations

import pytest

from readable_mcp.models import ErrorCode
from readable_mcp.validation import (
    MAX_URL_LENGTH,
    ValidationError,
    normalize_url,
    validate_url,
)


def test_accepts_normal_https_url(monkeypatch):
    # Force resolution to a public address so the test is offline + deterministic.
    monkeypatch.setattr(
        "readable_mcp.validation._resolve_addresses", lambda host: ["93.184.216.34"]
    )
    out = validate_url("https://example.com/path?q=1#frag")
    assert out == "https://example.com/path?q=1"  # fragment stripped


def test_accepts_http_scheme(monkeypatch):
    monkeypatch.setattr("readable_mcp.validation._resolve_addresses", lambda host: ["8.8.8.8"])
    assert validate_url("http://example.org/").startswith("http://example.org")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x"])
def test_rejects_non_http_schemes(url):
    with pytest.raises(ValidationError) as exc:
        validate_url(url)
    assert exc.value.code is ErrorCode.INVALID_URL


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://192.168.0.5/",
        "http://172.16.5.4/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
    ],
)
def test_blocks_private_and_loopback_hosts(url):
    with pytest.raises(ValidationError) as exc:
        validate_url(url)
    assert exc.value.code is ErrorCode.BLOCKED_HOST


def test_blocks_public_hostname_resolving_to_private_ip(monkeypatch):
    # The literal host is public-looking, but DNS points inward -> must be blocked.
    monkeypatch.setattr(
        "readable_mcp.validation._resolve_addresses", lambda host: ["169.254.169.254"]
    )
    with pytest.raises(ValidationError) as exc:
        validate_url("http://internal.evil.test/")
    assert exc.value.code is ErrorCode.BLOCKED_HOST


def test_rejects_missing_host():
    with pytest.raises(ValidationError) as exc:
        validate_url("https:///nohost")
    assert exc.value.code is ErrorCode.INVALID_URL


def test_rejects_empty_url():
    with pytest.raises(ValidationError):
        validate_url("   ")


def test_rejects_over_long_url():
    long_url = "https://example.com/" + "a" * (MAX_URL_LENGTH + 1)
    with pytest.raises(ValidationError) as exc:
        validate_url(long_url)
    assert exc.value.code is ErrorCode.INVALID_URL


def test_unresolvable_host_is_invalid(monkeypatch):
    import socket

    def boom(*args, **kwargs):
        raise socket.gaierror("name or service not known")

    monkeypatch.setattr("socket.getaddrinfo", boom)
    with pytest.raises(ValidationError) as exc:
        validate_url("https://does-not-exist.invalid/")
    assert exc.value.code is ErrorCode.INVALID_URL


def test_normalize_lowercases_scheme_and_host():
    assert normalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"
