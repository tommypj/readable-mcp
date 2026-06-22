"""Main-content extraction and metadata parsing.

``trafilatura`` does the heavy lifting — stripping nav/ads/boilerplate and pulling
title/author/date — and emits the requested format directly (markdown, text, or
cleaned HTML). If trafilatura cannot find a main body, we fall back to
``markdownify`` over the raw HTML so the caller still gets *something* usable rather
than an error, while genuinely empty pages raise a typed ``EXTRACTION_FAILED``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import trafilatura
from markdownify import markdownify
from trafilatura.metadata import extract_metadata

from .models import ErrorCode

# Map our public output_format values to trafilatura's.
_FORMAT_MAP = {"markdown": "markdown", "text": "txt", "html": "html"}
_VALID_FORMATS = frozenset(_FORMAT_MAP)
_TAG_RE = re.compile(r"<[^>]+>")


class ExtractionFailed(Exception):
    """Raised when no usable content can be extracted from a document."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = ErrorCode.EXTRACTION_FAILED
        self.message = message


@dataclass(slots=True)
class ExtractedDoc:
    """Extracted content plus metadata for one document."""

    title: str | None
    author: str | None
    published: str | None
    content: str
    word_count: int


def _word_count(text: str) -> int:
    """Count whitespace-delimited words in a plain-text string."""
    return len(text.split())


def _plain_text(value: str, output_format: str) -> str:
    """Best-effort plain text from extracted content, for word counting."""
    if output_format == "html":
        return _TAG_RE.sub(" ", value)
    return value


def extract_content(html: str, url: str, output_format: str) -> ExtractedDoc:
    """Extract main content + metadata from ``html``.

    Args:
        html: The raw HTML of the page.
        url: The (final) URL, used by trafilatura to resolve relative links/metadata.
        output_format: One of ``"markdown"``, ``"text"``, or ``"html"``.

    Returns:
        An :class:`ExtractedDoc`.

    Raises:
        ValueError: If ``output_format`` is not recognized.
        ExtractionFailed: If the page yields no usable content.
    """
    if output_format not in _VALID_FORMATS:
        raise ValueError(
            f"Unsupported output_format {output_format!r}; "
            f"expected one of {sorted(_VALID_FORMATS)}."
        )

    traf_format = _FORMAT_MAP[output_format]
    content = trafilatura.extract(
        html,
        url=url,
        output_format=traf_format,
        include_formatting=output_format != "text",
        include_tables=True,
        include_links=output_format != "text",
        favor_recall=True,
        with_metadata=False,
    )

    if not content or not content.strip():
        # Fallback: convert the whole document with markdownify.
        if output_format == "html":
            content = html
        elif output_format == "text":
            content = _TAG_RE.sub(" ", html)
        else:
            content = markdownify(html)
        content = content.strip() if content else ""

    if not content:
        raise ExtractionFailed(f"No extractable content found at {url}.")

    metadata = None
    try:
        metadata = extract_metadata(html)
    except Exception:  # noqa: BLE001 - metadata is best-effort, never fatal
        metadata = None

    title = getattr(metadata, "title", None) if metadata else None
    author = getattr(metadata, "author", None) if metadata else None
    date = getattr(metadata, "date", None) if metadata else None

    return ExtractedDoc(
        title=title,
        author=author,
        published=date,
        content=content,
        word_count=_word_count(_plain_text(content, output_format)),
    )
