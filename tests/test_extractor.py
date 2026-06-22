"""Tests for content extraction against saved HTML fixtures."""

from __future__ import annotations

import pytest

from readable_mcp.extractor import ExtractedDoc, ExtractionFailed, extract_content

ARTICLE_URL = "https://example.com/articles/production-grade-tooling"
BLOG_URL = "https://example.com/blog/caching"


def test_extracts_markdown_with_title_and_body(article_html):
    doc = extract_content(article_html, ARTICLE_URL, "markdown")
    assert isinstance(doc, ExtractedDoc)
    assert doc.title == "The Case for Production-Grade Tooling"
    assert doc.content.strip()
    assert doc.word_count > 30
    # Boilerplate should be stripped: the footer copyright must not survive.
    assert "All rights reserved" not in doc.content


def test_detects_author(article_html):
    doc = extract_content(article_html, ARTICLE_URL, "markdown")
    assert doc.author == "Dan Tomescu"


def test_text_format_has_no_markup(blog_html):
    doc = extract_content(blog_html, BLOG_URL, "text")
    assert doc.title == "Notes on Caching"
    assert "<" not in doc.content
    assert doc.word_count > 10


def test_html_format_returns_markup(article_html):
    doc = extract_content(article_html, ARTICLE_URL, "html")
    assert "<" in doc.content and ">" in doc.content


def test_rejects_unknown_format(article_html):
    with pytest.raises(ValueError):
        extract_content(article_html, ARTICLE_URL, "pdf")


def test_empty_document_raises_extraction_failed():
    with pytest.raises(ExtractionFailed):
        extract_content("<html><body></body></html>", "https://x.test/", "markdown")
