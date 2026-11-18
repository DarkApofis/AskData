import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from askdata_api.errors import ErrorCode, ProviderError, RateLimitedError

ENVELOPE_KEYS = {"code", "message", "request_id"}


class _Question(BaseModel):
    question: str


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    async def provider_down() -> None:
        try:
            raise RuntimeError("quota exceeded for key AIzaSyFAKE")
        except RuntimeError as upstream:
            raise ProviderError("gemini") from upstream

    async def rate_limited() -> None:
        raise RateLimitedError(retry_after_seconds=30)

    async def ask(body: _Question) -> dict[str, str]:
        return {"ok": body.question}

    app.add_api_route("/test/provider", provider_down)
    app.add_api_route("/test/rate", rate_limited)
    app.add_api_route("/test/ask", ask, methods=["POST"])
    return app


def _assert_envelope(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == code
    assert body["message"] == ErrorCode(code).message
    assert body["request_id"]


async def test_provider_error_hides_upstream_message(client: httpx.AsyncClient) -> None:
    response = await client.get("/test/provider")

    _assert_envelope(response, 503, "provider_unavailable")
    assert "quota" not in response.text
    assert "AIza" not in response.text
    assert "gemini" not in response.text


async def test_rate_limited_sets_retry_after(client: httpx.AsyncClient) -> None:
    response = await client.get("/test/rate")

    _assert_envelope(response, 429, "rate_limited")
    assert response.headers["Retry-After"] == "30"


async def test_404_uses_envelope(client: httpx.AsyncClient) -> None:
    _assert_envelope(await client.get("/nope"), 404, "not_found")


async def test_405_uses_envelope(client: httpx.AsyncClient) -> None:
    _assert_envelope(await client.delete("/healthz"), 405, "method_not_allowed")


@pytest.mark.parametrize(
    "content",
    [
        b'{"question": "SECRET_MARKER ",',  # malformed JSON
        b'{"wrong_field": "SECRET_MARKER"}',  # schema mismatch
        b'{"question": ["SECRET_MARKER"]}',  # wrong type
    ],
)
async def test_invalid_body_is_422_without_echo(client: httpx.AsyncClient, content: bytes) -> None:
    response = await client.post(
        "/test/ask", content=content, headers={"Content-Type": "application/json"}
    )

    _assert_envelope(response, 422, "validation_error")
    assert "SECRET_MARKER" not in response.text


def test_every_code_has_status_and_message() -> None:
    expected = {
        "validation_error": 422,
        "payload_too_large": 413,
        "rate_limited": 429,
        "provider_unavailable": 503,
        "request_timeout": 504,
        "not_found": 404,
        "method_not_allowed": 405,
        "cors_rejected": 403,
        "internal_error": 500,
    }

    assert {code.value: code.status for code in ErrorCode} == expected
    assert all(code.message.endswith(".") for code in ErrorCode)
