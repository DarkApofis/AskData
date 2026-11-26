"""Typed provider failures. Subclasses of B1's errors, so the existing handlers map
them to the envelope; `provider` and `reason` are for logs, never the client."""

from typing import Literal

from askdata_api.errors import ProviderError, RateLimitedError

OutputErrorReason = Literal["invalid_json", "schema_mismatch", "blocked", "truncated", "empty"]


class ProviderOutputError(ProviderError):
    """The provider answered, but not with usable output."""

    def __init__(self, provider: str, reason: OutputErrorReason) -> None:
        super().__init__(provider)
        self.reason = reason


class ProviderRateLimited(RateLimitedError):  # noqa: N818 - name requested in the task
    """The provider throttled us. Surfaces as 429 with Retry-After when known."""

    def __init__(self, provider: str, retry_after_seconds: int | None) -> None:
        super().__init__(retry_after_seconds)
        self.provider = provider
