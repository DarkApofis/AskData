import asyncio
import json
import logging

import httpx
import pytest
from fastapi import FastAPI, Request

from askdata_api.app import create_app
from askdata_api.settings import Settings
from tests.conftest import make_settings

LIMIT = 65_536


async def _echo_size(request: Request) -> dict[str, int]:
    return {"size": len(await request.body())}


async def _slow() -> dict[str, str]:
    await asyncio.sleep(1)
    return {"status": "late"}


@pytest.fixture
def settings() -> Settings:
    return make_settings(request_timeout_seconds=0.05)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    app = create_app(settings)
    app.add_api_route("/test/echo-size", _echo_size, methods=["POST"])
    app.add_api_route("/test/slow", _slow)
    return app


async def _raw_post(
    app: FastAPI, headers: list[tuple[bytes, bytes]], chunks: list[bytes]
) -> tuple[int, dict[str, str], bytes]:
    """Drive the ASGI app directly so the test controls headers and body framing."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/test/echo-size",
        "raw_path": b"/test/echo-size",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), (b"content-type", b"application/octet-stream"), *headers],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    pending = [
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
        for i, chunk in enumerate(chunks)
    ]
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        if pending:
            return pending.pop(0)
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    async with asyncio.timeout(5):
        await app(scope, receive, send)  # type: ignore[arg-type]

    start = next(m for m in sent if m["type"] == "http.response.start")
    raw_headers = start["headers"]
    assert isinstance(raw_headers, list)
    body = b"".join(m["body"] for m in sent if m["type"] == "http.response.body")  # type: ignore[misc]
    return (
        int(start["status"]),  # type: ignore[call-overload]
        {k.decode().lower(): v.decode() for k, v in raw_headers},
        body,
    )


def _assert_413(status: int, body: bytes) -> None:
    assert status == 413
    assert json.loads(body)["code"] == "payload_too_large"


async def test_content_length_over_limit_is_413(client: httpx.AsyncClient) -> None:
    response = await client.post("/test/echo-size", content=b"x" * (LIMIT + 1))

    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"
    assert response.headers["cache-control"] == "no-store"


async def test_body_at_limit_is_accepted(client: httpx.AsyncClient) -> None:
    response = await client.post("/test/echo-size", content=b"x" * LIMIT)

    assert response.status_code == 200
    assert response.json() == {"size": LIMIT}


async def test_chunked_body_over_limit_is_413(app: FastAPI) -> None:
    chunks = [b"x" * 16_384] * 5  # 80 KB, no Content-Length

    status, _, body = await _raw_post(app, [(b"transfer-encoding", b"chunked")], chunks)

    _assert_413(status, body)


async def test_chunked_body_under_limit_is_accepted(app: FastAPI) -> None:
    status, _, body = await _raw_post(app, [], [b"x" * 1000, b"y" * 1000])

    assert status == 200
    assert json.loads(body) == {"size": 2000}


async def test_lying_content_length_is_413(app: FastAPI) -> None:
    chunks = [b"x" * 10_000] * 7  # 70 KB streamed, 10 bytes declared

    status, _, body = await _raw_post(app, [(b"content-length", b"10")], chunks)

    _assert_413(status, body)


@pytest.mark.parametrize("value", [b"abc", b"-1", b"1e9", b""])
async def test_invalid_content_length_is_413(app: FastAPI, value: bytes) -> None:
    status, _, body = await _raw_post(app, [(b"content-length", value)], [b"x"])

    _assert_413(status, body)


async def test_slow_request_is_504(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        response = await client.get("/test/slow", headers={"X-Request-ID": "slow-req-0001"})

    assert response.status_code == 504
    assert response.json() == {
        "code": "request_timeout",
        "message": "The request took too long.",
        "request_id": "slow-req-0001",
    }
    assert response.headers["cache-control"] == "no-store"
    events = [r.msg for r in caplog.records]
    assert "request_timeout" in events
    assert "unhandled_error" not in events


async def test_inner_timeout_error_is_not_mistaken_for_ours(app: FastAPI) -> None:
    async def inner_timeout() -> None:
        raise TimeoutError

    app.add_api_route("/test/inner-timeout", inner_timeout)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/test/inner-timeout")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
