"""Structured JSON logging with request correlation (NFR-0005).

CloudWatch parses one JSON object per line into queryable fields, so every record
is emitted as JSON and the current request ID is attached automatically.

Note: this module shadows the stdlib name inside the `shared` package only.
Absolute imports mean `import logging` below still resolves to the stdlib.
"""

from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from typing import Any

# Set once per invocation by the error decorator. A ContextVar (not a global)
# so the value cannot leak between concurrent executions.
_request_id: ContextVar[str] = ContextVar("request_id", default="-")

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str:
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    """Render a record as a single JSON line, including any `extra=` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "requestId": get_request_id(),
        }

        # Anything passed via extra= lands on the record but not on a fresh one.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def get_logger(name: str) -> logging.Logger:
    """Return a JSON-formatted logger.

    Lambda installs its own root handler, so we attach our own and stop
    propagating — otherwise every line is emitted twice, once unformatted.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    return logger
