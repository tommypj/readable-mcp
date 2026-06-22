"""URL validation and SSRF protection.

This is the security boundary of the server. Before any outbound request we:

1. Reject anything that is not ``http``/``https``.
2. Reject malformed URLs, missing hosts, and absurdly long inputs.
3. Resolve the hostname and refuse to connect to any address that lands in
   private, loopback, link-local, or otherwise reserved space — so ``localhost``,
   raw private IPs, *and* public hostnames that resolve inward are all blocked.

The resolution step is what makes this a real SSRF guard rather than a string check:
an attacker cannot smuggle ``http://internal.evil.test`` past us by pointing its DNS
record at ``169.254.169.254``.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from .models import ErrorCode

MAX_URL_LENGTH = 2048
_ALLOWED_SCHEMES = frozenset({"http", "https"})


class ValidationError(Exception):
    """Raised when a URL is unsafe or malformed. Carries a typed error code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _resolve_addresses(host: str) -> list[str]:
    """Resolve a hostname to all of its IP addresses (v4 and v6).

    Raises :class:`ValidationError` (``INVALID_URL``) when resolution fails.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValidationError(
            ErrorCode.INVALID_URL, f"Could not resolve host {host!r}: {exc}"
        ) from exc
    return [info[4][0] for info in infos]


def _is_public_ip(ip: str) -> bool:
    """Return True only for globally routable unicast addresses."""
    addr = ipaddress.ip_address(ip)
    # ``is_global`` already excludes private/loopback/link-local/reserved/multicast,
    # but we add explicit checks for clarity and defense in depth.
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return False
    return addr.is_global


def normalize_url(url: str) -> str:
    """Return a canonical form of ``url`` for cache keying and comparison.

    Lowercases the scheme/host and strips the fragment; preserves path, query,
    and any explicit port. Does **not** perform validation.
    """
    parts = urlsplit(url.strip())
    netloc = parts.netloc
    if "@" not in netloc:
        # Lowercase host[:port] only when there is no userinfo to mangle.
        netloc = netloc.lower()
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))


def validate_url(url: str) -> str:
    """Validate ``url`` and return it normalized, or raise :class:`ValidationError`.

    Performs scheme, structure, length, and SSRF (resolved-IP) checks. The returned
    value is the normalized URL suitable for fetching and cache keying.
    """
    if not url or not url.strip():
        raise ValidationError(ErrorCode.INVALID_URL, "URL is empty.")

    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise ValidationError(
            ErrorCode.INVALID_URL,
            f"URL exceeds maximum length of {MAX_URL_LENGTH} characters.",
        )

    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValidationError(
            ErrorCode.INVALID_URL,
            f"Unsupported scheme {parts.scheme!r}; only http and https are allowed.",
        )

    host = parts.hostname
    if not host:
        raise ValidationError(ErrorCode.INVALID_URL, "URL has no host component.")

    # If the host is a literal IP, check it directly; otherwise resolve it.
    try:
        literal = ipaddress.ip_address(host)
        candidates = [str(literal)]
    except ValueError:
        candidates = _resolve_addresses(host)

    for ip in candidates:
        if not _is_public_ip(ip):
            raise ValidationError(
                ErrorCode.BLOCKED_HOST,
                f"Refusing to fetch {host!r}: resolves to non-public address {ip}.",
            )

    return normalize_url(url)
