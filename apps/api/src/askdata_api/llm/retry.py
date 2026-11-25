"""Retry helpers shared by provider adapters."""

import math
import random
import re
from collections.abc import Callable

import httpx

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Upper bound for a provider-supplied wait; it is also passed on to our client.
MAX_RETRY_AFTER_SECONDS = 3600
_RETRY_INFO_TYPE = "type.googleapis.com/google.rpc.RetryInfo"
_DURATION = re.compile(r"(\d+(?:\.\d+)?)s")


def backoff_delay(
    attempt: int,
    *,
    base: float = 0.5,
    cap: float = 4.0,
    rand: Callable[[float, float], float] = random.uniform,
) -> float:
    """Full-jitter exponential backoff for retry number `attempt` (0-based)."""
    return rand(0, min(cap, base * 2**attempt))


def parse_retry_after(response: httpx.Response) -> int | None:
    """Seconds to wait, from a `Retry-After: <seconds>` header or a Google
    `RetryInfo.retryDelay` ("30s") in the error body. None when absent or malformed."""
    header = response.headers.get("retry-after", "").strip()
    if header.isascii() and header.isdigit():
        return _clamp(_int_or_none(header))
    try:
        details = response.json()["error"]["details"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(details, list):
        return None
    for detail in details:
        if isinstance(detail, dict) and detail.get("@type") == _RETRY_INFO_TYPE:
            match = _DURATION.fullmatch(str(detail.get("retryDelay", "")))
            if match:
                seconds = float(match.group(1))
                return _clamp(max(1, math.ceil(seconds)) if math.isfinite(seconds) else None)
    return None


def _int_or_none(digits: str) -> int | None:
    try:
        return int(digits)
    except ValueError:  # beyond Python's int-from-str digit limit
        return None


def _clamp(seconds: int | None) -> int | None:
    return None if seconds is None else min(seconds, MAX_RETRY_AFTER_SECONDS)
