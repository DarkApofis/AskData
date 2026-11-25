"""Per-request context shared by middleware, error handlers and logging."""

from contextvars import ContextVar
from dataclasses import dataclass

UNKNOWN_REQUEST_ID = "unknown"

request_id_var: ContextVar[str] = ContextVar("request_id", default=UNKNOWN_REQUEST_ID)


@dataclass
class ErrorCodeSlot:
    """Mutable holder so the access log can report the error code chosen deeper
    in the stack (a plain ContextVar set by a callee is not seen by the caller)."""

    code: str | None = None


error_code_slot_var: ContextVar[ErrorCodeSlot | None] = ContextVar("error_code_slot", default=None)
