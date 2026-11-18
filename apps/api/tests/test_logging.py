import json
import logging
import sys

import pytest

from askdata_api.logging_setup import ALLOWED_LOG_FIELDS, JsonFormatter, configure_logging
from askdata_api.request_context import request_id_var


def _record(msg: str, *args: object, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("askdata.test", logging.INFO, __file__, 1, msg, args, None)
    record.__dict__.update(extra)
    return record


def test_formatter_emits_json_with_request_id() -> None:
    token = request_id_var.set("req-12345678")
    try:
        line = JsonFormatter().format(_record("request", status=200, latency_ms=3.2))
    finally:
        request_id_var.reset(token)

    data = json.loads(line)
    assert data["event"] == "request"
    assert data["level"] == "INFO"
    assert data["logger"] == "askdata.test"
    assert data["request_id"] == "req-12345678"
    assert data["status"] == 200
    assert data["latency_ms"] == 3.2
    assert data["ts"].endswith("Z")


@pytest.mark.parametrize(
    "content",
    ["¿Cuántas ventas hubo en marzo?", "How many sales were there in March?"],
)
def test_formatter_drops_unknown_extras(content: str) -> None:
    line = JsonFormatter().format(_record("request", question=content, sql=content, status=200))

    data = json.loads(line)
    assert data["status"] == 200
    assert "question" not in data
    assert "sql" not in data
    assert content not in line


@pytest.mark.parametrize("content", ["SELECT * FROM ventas", "¿Total por región?"])
def test_formatter_does_not_interpolate_args(content: str) -> None:
    line = JsonFormatter().format(_record("user asked %s", content))

    assert content not in line
    assert json.loads(line)["event"] == "user asked %s"


def test_formatter_omits_exception_message() -> None:
    try:
        raise ValueError("SECRET_SQL SELECT 1")
    except ValueError:
        record = logging.LogRecord(
            "askdata.test", logging.ERROR, __file__, 1, "unhandled_error", None, sys.exc_info()
        )

    line = JsonFormatter().format(record)

    data = json.loads(line)
    assert "SECRET_SQL" not in line
    assert data["error_type"] == "ValueError"
    assert data["frames"]
    assert all(frame.count(":") >= 2 for frame in data["frames"])


def test_allowed_fields_are_metadata_only() -> None:
    assert (
        frozenset(
            {
                "method",
                "path",
                "status",
                "latency_ms",
                "code",
                "provider",
                "state",
                "error_type",
                "frames",
            }
        )
        == ALLOWED_LOG_FIELDS
    )


def test_configure_logging_installs_json_on_root_and_silences_access() -> None:
    configure_logging("INFO")

    root = logging.getLogger()
    assert any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)
    assert logging.getLogger("uvicorn.access").disabled
    assert not logging.getLogger("uvicorn.error").handlers
    assert logging.getLogger("uvicorn.error").propagate
