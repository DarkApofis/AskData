"""Last line of defence: any exception escaping the app becomes `internal_error`.

The exception is never re-raised, so neither Starlette nor uvicorn prints its
message or traceback. We log its type and frame locations only.
"""

import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from askdata_api.errors import ErrorCode, error_response
from askdata_api.logging_setup import exception_fields

logger = logging.getLogger(__name__)


class ErrorBoundaryMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            logger.error("unhandled_error", extra=exception_fields(exc))
            if not response_started:
                await error_response(ErrorCode.INTERNAL_ERROR)(scope, receive, send)
                return
            # Too late for a second response. Make the server abort the connection
            # (so the client sees truncation, not a "complete" body) with an exception
            # that carries no message and no chained cause.
            raise ResponseAbortedError from None


class ResponseAbortedError(Exception):
    """Raised after a mid-response failure; intentionally empty."""
