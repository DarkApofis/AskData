"""Request ID propagation plus one metadata-only access log line per request."""

import logging
import re
import time
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from askdata_api.request_context import ErrorCodeSlot, error_code_slot_var, request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
# Strict on purpose: the value lands in logs and response bodies.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")
_MAX_LOGGED_PATH = 200

logger = logging.getLogger("askdata_api.access")


def _request_id(scope: Scope) -> str:
    incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
    if incoming is not None and _VALID_REQUEST_ID.fullmatch(incoming):
        return incoming
    return uuid4().hex


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        id_token = request_id_var.set(request_id)
        slot = ErrorCodeSlot()
        slot_token = error_code_slot_var.set(slot)
        status: int | None = None
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            extra: dict[str, object] = {
                "method": scope["method"],
                "path": scope["path"][:_MAX_LOGGED_PATH],  # never the query string
                "status": status,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
            if slot.code is not None:
                extra["code"] = slot.code
            logger.info("request", extra=extra)
            error_code_slot_var.reset(slot_token)
            request_id_var.reset(id_token)
