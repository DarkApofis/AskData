"""Structured JSON logs that carry metadata only.

Guarantees, enforced here rather than by convention at call sites:
- the event is the literal message template; `%` arguments are never interpolated;
- only allowlisted `extra` keys are emitted, anything else is dropped;
- exceptions are reduced to their type and frame locations, never their message.
So question text, sample rows, SQL and settings values cannot reach a log line.
"""

import json
import logging
import os
import sys
import traceback
from datetime import UTC, datetime

from askdata_api.request_context import request_id_var

ALLOWED_LOG_FIELDS = frozenset(
    {"method", "path", "status", "latency_ms", "code", "provider", "state", "error_type", "frames"}
)


def exception_fields(exc: BaseException) -> dict[str, object]:
    """Type and frame locations of an exception; never its message or locals."""
    return {
        "error_type": type(exc).__name__,
        "frames": [
            f"{os.path.basename(frame.filename)}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_tb(exc.__traceback__)
        ],
    }


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": str(record.msg),
            "request_id": request_id_var.get(),
        }
        for key in ALLOWED_LOG_FIELDS:
            if key in record.__dict__:
                data[key] = record.__dict__[key]
        if record.exc_info and record.exc_info[1] is not None:
            data.update(exception_fields(record.exc_info[1]))
        return json.dumps(data, ensure_ascii=False, default=str)


class _JsonHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Marker type so configure_logging only replaces handlers it installed."""


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h, _JsonHandler)]:
        root.removeHandler(handler)
    handler = _JsonHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # Our request middleware writes the access line; uvicorn's includes the query string.
    logging.getLogger("uvicorn.access").disabled = True
