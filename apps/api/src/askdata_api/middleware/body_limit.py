"""Hard cap on request body size.

Checked twice: up front against a declared Content-Length, and while streaming,
so chunked uploads or a lying Content-Length cannot get past it.
"""

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from askdata_api.errors import ErrorCode, error_response


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None and not (declared.isdigit() and int(declared) <= self.max_bytes):
            await error_response(ErrorCode.PAYLOAD_TOO_LARGE)(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # HTTPException (not a custom type): FastAPI re-raises it from body
                    # parsing, whereas any other exception there becomes a 400.
                    raise HTTPException(status_code=413)
            return message

        await self.app(scope, limited_receive, send)
