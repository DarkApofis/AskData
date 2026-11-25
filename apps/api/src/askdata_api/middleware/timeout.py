"""Per-request time budget; an over-budget request gets `request_timeout`."""

import asyncio
import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from askdata_api.errors import ErrorCode, error_response
from askdata_api.middleware.error_boundary import ResponseAbortedError

logger = logging.getLogger(__name__)


class TimeoutMiddleware:
    def __init__(self, app: ASGIApp, seconds: float) -> None:
        self.app = app
        self.seconds = seconds

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
            async with asyncio.timeout(self.seconds) as budget:
                await self.app(scope, receive, tracking_send)
        except TimeoutError:
            if not budget.expired():
                raise  # a TimeoutError from inside the app is the app's own error
            logger.warning("request_timeout", extra={"code": ErrorCode.REQUEST_TIMEOUT.value})
            if response_started:
                raise ResponseAbortedError from None
            await error_response(ErrorCode.REQUEST_TIMEOUT)(scope, receive, send)
