import asyncio
import logging
import re
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}
GENERATED_ID = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    async def boom() -> None:
        raise RuntimeError("SECRET_MARKER")

    async def stream_then_fail() -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            yield b"partial"
            raise RuntimeError("SECRET_MARKER mid-stream")

        return StreamingResponse(chunks())

    async def echo(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    app.add_api_route("/test/boom", boom)
    app.add_api_route("/test/stream", stream_then_fail)
    app.add_api_route("/test/echo", echo, methods=["POST"])
    return app


@pytest.mark.parametrize("path", ["/healthz", "/nope", "/test/boom"])
async def test_security_headers_on_200_404_500(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)

    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


async def test_unhandled_error_is_500_without_traceback(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        response = await client.get("/test/boom")

    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"code", "message", "request_id"}
    assert body["code"] == "internal_error"
    assert "Traceback" not in response.text
    assert "SECRET_MARKER" not in response.text
    errors = [r for r in caplog.records if r.msg == "unhandled_error"]
    assert len(errors) == 1
    assert errors[0].__dict__["error_type"] == "RuntimeError"
    assert "SECRET_MARKER" not in caplog.text


async def test_error_after_response_started_does_not_send_twice(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    async with asyncio.timeout(5):
        response = await client.get("/test/stream")

    assert response.status_code == 200
    assert response.content.startswith(b"partial")
    assert [r.msg for r in caplog.records].count("unhandled_error") == 1


async def test_valid_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    response = await client.get("/nope", headers={"X-Request-ID": "abcd1234-ok"})

    assert response.headers["X-Request-ID"] == "abcd1234-ok"
    assert response.json()["request_id"] == "abcd1234-ok"


async def test_request_id_generated_when_absent(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")

    assert GENERATED_ID.match(response.headers["X-Request-ID"])


@pytest.mark.parametrize(
    "bad_id", ["x\nINJECT-value", "a" * 65, "short", "has space 123", "ünïcode-123"]
)
async def test_malicious_request_id_is_replaced(client: httpx.AsyncClient, bad_id: str) -> None:
    response = await client.get("/nope", headers={b"X-Request-ID": bad_id.encode()})

    assert GENERATED_ID.match(response.headers["X-Request-ID"])
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.parametrize(
    "question", ["¿Cuál es el total de ventas?", "What is the total of sales?"]
)
async def test_access_log_has_metadata_only(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture, question: str
) -> None:
    with caplog.at_level(logging.INFO):
        response = await client.post("/test/echo?q=SECRET_QUERY", json={"question": question})

    assert response.status_code == 200
    access = [r for r in caplog.records if r.msg == "request"]
    assert len(access) == 1
    fields = access[0].__dict__
    assert fields["method"] == "POST"
    assert fields["path"] == "/test/echo"
    assert fields["status"] == 200
    assert isinstance(fields["latency_ms"], float)
    # Only our loggers: httpx's client-side record (the test client) naturally has the URL.
    for record in [r for r in caplog.records if r.name.startswith("askdata_api")]:
        assert question not in str(record.__dict__)
        assert "SECRET_QUERY" not in str(record.__dict__)


async def test_access_log_carries_error_code(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        await client.get("/nope")

    access = [r for r in caplog.records if r.msg == "request"]
    assert access[0].__dict__["status"] == 404
    assert access[0].__dict__["code"] == "not_found"


async def test_options_without_origin_is_405_envelope(client: httpx.AsyncClient) -> None:
    response = await client.options("/healthz")

    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"
