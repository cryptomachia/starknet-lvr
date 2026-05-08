"""
Structured logging — JSON lines to stdout, parseable by log aggregators.

Usage:
    from lvr_lab.observability.logging import get_logger
    log = get_logger("indexer.ekubo")
    log.info("processed_chunk", events=42, blocks=30, duration_ms=1850)

Production: pipe stdout to Loki / Datadog / CloudWatch via container runtime.
Dev: pipe through `jq` for human-readable: `python ... | jq -r .msg`.
"""

from __future__ import annotations
import json
import logging
import sys
import time
from typing import Any


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Attach extras
        for key, value in record.__dict__.items():
            if key in ("args", "msg", "levelname", "name", "created",
                       "filename", "lineno", "module", "funcName",
                       "msecs", "relativeCreated", "thread", "threadName",
                       "process", "processName", "pathname", "stack_info",
                       "exc_info", "exc_text", "levelno"):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    _configure()
    return logging.getLogger(name)


def log_with_context(logger: logging.Logger, level: int, msg: str, **kwargs) -> None:
    """Helper: log.info("event", key=value, ...) but with named arg sugar."""
    logger.log(level, msg, extra=kwargs)
