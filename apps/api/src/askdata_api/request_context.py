"""Per-request context shared by middleware, error handlers and logging."""

from contextvars import ContextVar

UNKNOWN_REQUEST_ID = "unknown"

request_id_var: ContextVar[str] = ContextVar("request_id", default=UNKNOWN_REQUEST_ID)
