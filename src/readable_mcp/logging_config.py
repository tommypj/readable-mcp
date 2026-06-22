"""Structured JSON logging with per-request correlation ids.

Logs are single-line JSON written to stderr (stdout is reserved for the MCP stdio
transport). We log operational metadata only — never full response bodies or secrets.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render log records as compact single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge any structured fields passed via `extra=`.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the package logger (idempotent)."""
    logger = logging.getLogger("readable_mcp")
    logger.setLevel(level.upper())
    logger.propagate = False
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)


def get_logger() -> logging.Logger:
    """Return the package logger."""
    return logging.getLogger("readable_mcp")


def new_request_id() -> str:
    """Return a short unique id for correlating the logs of one tool call."""
    return uuid.uuid4().hex[:12]
