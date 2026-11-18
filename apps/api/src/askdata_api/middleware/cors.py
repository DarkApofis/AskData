"""Strict CORS: exact-origin allowlist, GET/POST only, no credentials.

Stock Starlette answers a rejected preflight with a plain-text 400 that still
carries Access-Control-Allow-Methods/Headers/Max-Age, and adds
Access-Control-Expose-Headers to responses for any origin. Both are tightened
here: a disallowed origin never receives an Access-Control-* header.
"""

from collections.abc import Sequence

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Send

from askdata_api.errors import ErrorCode, error_response


class StrictCORSMiddleware(CORSMiddleware):
    def __init__(self, app: ASGIApp, allowed_origins: Sequence[str]) -> None:
        super().__init__(
            app,
            allow_origins=list(allowed_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Retry-After"],
            allow_credentials=False,
            max_age=600,
        )

    def preflight_response(self, request_headers: Headers) -> Response:
        response = super().preflight_response(request_headers)
        if response.status_code != 200:
            return error_response(ErrorCode.CORS_REJECTED)
        return response

    async def send(self, message: Message, send: Send, request_headers: Headers) -> None:
        origin = request_headers.get("origin")
        if (
            message["type"] == "http.response.start"
            and origin is not None
            and not self.is_allowed_origin(origin=origin)
        ):
            await send(message)
            return
        await super().send(message, send, request_headers)
