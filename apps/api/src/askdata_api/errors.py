"""The single error envelope: {code, message, request_id}.

`code` is a stable machine key the client localizes (es/en); `message` is a fixed
English fallback. No caller, framework or provider text ever reaches the body.
"""

import logging
from collections.abc import Mapping
from enum import StrEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from askdata_api.request_context import request_id_var

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    REQUEST_TIMEOUT = "request_timeout"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    CORS_REJECTED = "cors_rejected"
    INTERNAL_ERROR = "internal_error"

    @property
    def status(self) -> int:
        return _SPEC[self][0]

    @property
    def message(self) -> str:
        return _SPEC[self][1]


_SPEC: dict[ErrorCode, tuple[int, str]] = {
    ErrorCode.VALIDATION_ERROR: (422, "The request is invalid."),
    ErrorCode.PAYLOAD_TOO_LARGE: (413, "The request body is too large."),
    ErrorCode.RATE_LIMITED: (429, "Too many requests. Try again later."),
    ErrorCode.PROVIDER_UNAVAILABLE: (503, "The language model is unavailable. Try again later."),
    ErrorCode.REQUEST_TIMEOUT: (504, "The request took too long."),
    ErrorCode.NOT_FOUND: (404, "Not found."),
    ErrorCode.METHOD_NOT_ALLOWED: (405, "Method not allowed."),
    ErrorCode.CORS_REJECTED: (403, "Origin or method not allowed."),
    ErrorCode.INTERNAL_ERROR: (500, "Something went wrong."),
}


class ErrorResponse(BaseModel):
    code: ErrorCode
    message: str
    request_id: str


class AppError(Exception):
    """Base for errors we raise on purpose. The client only ever sees `code`."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def headers(self) -> dict[str, str]:
        return {}


class RateLimitedError(AppError):
    code = ErrorCode.RATE_LIMITED

    def __init__(self, retry_after_seconds: int | None = None) -> None:
        super().__init__()
        self.retry_after_seconds = retry_after_seconds

    def headers(self) -> dict[str, str]:
        if self.retry_after_seconds is None:
            return {}
        return {"Retry-After": str(self.retry_after_seconds)}


class ProviderError(AppError):
    """An LLM provider failed. `provider` is for logs only, never for the client."""

    code = ErrorCode.PROVIDER_UNAVAILABLE

    def __init__(self, provider: str) -> None:
        super().__init__()
        self.provider = provider


def error_response(code: ErrorCode, headers: Mapping[str, str] | None = None) -> JSONResponse:
    """Build the one and only error body shape."""
    body = ErrorResponse(code=code, message=code.message, request_id=request_id_var.get())
    return JSONResponse(body.model_dump(mode="json"), status_code=code.status, headers=headers)


_HTTP_STATUS_CODES: dict[int, ErrorCode] = {
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
}

# Framework headers that are safe and meaningful to forward.
_FORWARDED_HEADERS = frozenset({"allow"})


async def _handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):  # unreachable by registration; fail closed
        return error_response(ErrorCode.INTERNAL_ERROR)
    extra: dict[str, object] = {"code": exc.code.value}
    if isinstance(exc, ProviderError):
        extra["provider"] = exc.provider
    logger.warning("app_error", extra=extra)
    return error_response(exc.code, exc.headers())


async def _handle_validation_error(_: Request, __: Exception) -> JSONResponse:
    # No field details: pydantic errors echo the offending input.
    return error_response(ErrorCode.VALIDATION_ERROR)


async def _handle_http_exception(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):  # unreachable; fail closed
        return error_response(ErrorCode.INTERNAL_ERROR)
    if exc.status_code in _HTTP_STATUS_CODES:
        code = _HTTP_STATUS_CODES[exc.status_code]
    elif 400 <= exc.status_code < 500:
        code = ErrorCode.VALIDATION_ERROR
    else:
        code = ErrorCode.INTERNAL_ERROR
    headers = {k: v for k, v in (exc.headers or {}).items() if k.lower() in _FORWARDED_HEADERS}
    return error_response(code, headers)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
