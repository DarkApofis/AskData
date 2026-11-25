import json

import httpx
import pytest

from askdata_api.llm.retry import RETRYABLE_STATUS, backoff_delay, parse_retry_after


def _retry_info(delay: str) -> bytes:
    return json.dumps(
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}
                ],
            }
        }
    ).encode()


def test_retryable_statuses() -> None:
    assert frozenset({429, 500, 502, 503, 504}) == RETRYABLE_STATUS


@pytest.mark.parametrize(("attempt", "upper"), [(0, 0.5), (1, 1.0), (2, 2.0), (3, 4.0), (10, 4.0)])
def test_backoff_is_full_jitter_and_capped(attempt: int, upper: float) -> None:
    bounds: list[tuple[float, float]] = []

    def rand(low: float, high: float) -> float:
        bounds.append((low, high))
        return high

    assert backoff_delay(attempt, rand=rand) == upper
    assert bounds == [(0, upper)]


def test_retry_after_header_seconds() -> None:
    assert parse_retry_after(httpx.Response(429, headers={"Retry-After": "2"})) == 2


@pytest.mark.parametrize(("delay", "expected"), [("30s", 30), ("1.5s", 2), ("0.2s", 1)])
def test_retry_after_from_retry_info(delay: str, expected: int) -> None:
    assert parse_retry_after(httpx.Response(429, content=_retry_info(delay))) == expected


def test_header_wins_over_body() -> None:
    response = httpx.Response(429, headers={"Retry-After": "3"}, content=_retry_info("30s"))

    assert parse_retry_after(response) == 3


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        httpx.Response(429, headers={"Retry-After": "-3"}),
        httpx.Response(429, headers={"Retry-After": "abc"}),
        httpx.Response(429, content=b"<html>busy</html>"),
        httpx.Response(429, content=_retry_info("soon")),
        httpx.Response(429, content=b'{"error": {"details": "not-a-list"}}'),
        httpx.Response(429, content=b"[1, 2]"),
        httpx.Response(429),
    ],
)
def test_retry_after_garbage_is_none(response: httpx.Response) -> None:
    assert parse_retry_after(response) is None
