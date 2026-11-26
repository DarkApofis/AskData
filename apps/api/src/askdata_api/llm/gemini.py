"""Gemini adapter over plain httpx (no SDK: one dependency we already have, full
control of where the key goes, every path testable with httpx.MockTransport).

Errors are always raised *outside* `except` blocks, so no httpx exception (request,
response) or pydantic error (model output) is chained onto what we raise.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from askdata_api.errors import ProviderError
from askdata_api.llm.base import Completion, Usage
from askdata_api.llm.errors import OutputErrorReason, ProviderOutputError, ProviderRateLimited
from askdata_api.llm.retry import RETRYABLE_STATUS, backoff_delay, parse_retry_after

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# A 429 asking us to wait longer than this fails fast instead of eating the request budget.
MAX_RETRY_WAIT_SECONDS = 4.0

logger = logging.getLogger(__name__)


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = 5.0,
        max_attempts: int = 3,
        max_output_tokens: int = 2048,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_attempts = max_attempts
        self._max_output_tokens = max_output_tokens
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"GeminiProvider(model={self._model!r})"

    async def complete[T: BaseModel](
        self, *, system: str, user: str, output_model: type[T]
    ) -> Completion[T]:
        body = self._body(system, user)
        body["generationConfig"] |= {
            "responseMimeType": "application/json",
            "responseJsonSchema": output_model.model_json_schema(),
        }
        started = time.perf_counter()
        response = await self._send("generateContent", body)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)

        data = _json_or(response.content, _INVALID)
        if data is _INVALID:
            raise self._output_error("invalid_json")
        text = self._candidate_text(data)
        parsed = _json_or(text.encode(), _INVALID)
        if parsed is _INVALID:
            raise self._output_error("invalid_json")
        output = _validate_or_none(output_model, parsed)
        if output is None:
            raise self._output_error("schema_mismatch")

        usage = _dict(_dict(data).get("usageMetadata"))
        return Completion(
            output=output,
            usage=Usage(
                latency_ms=latency_ms,
                input_tokens=_int_or_none(usage.get("promptTokenCount")),
                output_tokens=_int_or_none(usage.get("candidatesTokenCount")),
            ),
            provider=self.name,
            model=self._model,
        )

    async def stream_text(self, *, system: str, user: str) -> AsyncIterator[str]:
        # Retries happen only inside _send, i.e. before the first delta is yielded.
        response = await self._send("streamGenerateContent", self._body(system, user), stream=True)
        try:
            lines = response.aiter_lines()
            finish: object = None
            yielded = False
            while (line := await self._next_line(lines)) is not None:
                if not line.startswith("data:"):
                    continue  # blank separators, ": comments", "event:" / "id:" fields
                event = _json_or(line.removeprefix("data:").strip().encode(), _INVALID)
                if not isinstance(event, dict):
                    raise self._output_error("invalid_json")
                if "error" in event:  # in-band error event: the stream failed upstream
                    logger.warning("provider_stream_error", extra={"provider": self.name})
                    raise ProviderError(self.name)
                if _dict(event.get("promptFeedback")).get("blockReason"):
                    raise self._output_error("blocked")
                candidates = event.get("candidates")
                candidate = (
                    _dict(candidates[0]) if isinstance(candidates, list) and candidates else {}
                )
                finish = candidate.get("finishReason") or finish
                if text := _part_text(candidate):
                    yielded = True
                    yield text
            if finish == "MAX_TOKENS":
                raise self._output_error("truncated")
            if finish not in (None, "STOP"):
                raise self._output_error("blocked")
            if not yielded:
                raise self._output_error("empty")
            if finish is None:  # text but no final finishReason: the stream was cut
                raise self._output_error("truncated")
        finally:
            await response.aclose()

    async def _next_line(self, lines: AsyncIterator[str]) -> str | None:
        """Next SSE line, None at the end; a dropped stream is a ProviderError."""
        line: str | None = None
        dropped = False
        try:
            line = await anext(lines)
        except StopAsyncIteration:
            pass
        except httpx.HTTPError as exc:
            logger.warning(
                "provider_transport_error",
                extra={"provider": self.name, "error_type": type(exc).__name__},
            )
            dropped = True
        if dropped:
            raise ProviderError(self.name)
        return line

    def _body(self, system: str, user: str) -> dict[str, Any]:
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": self._max_output_tokens},
        }

    async def _send(
        self, method: str, body: dict[str, Any], *, stream: bool = False
    ) -> httpx.Response:
        """POST with bounded retries for 429/5xx only. Returns a 200 response."""
        request = self._client.build_request(
            "POST",
            f"{GEMINI_BASE_URL}/models/{self._model}:{method}",
            params={"alt": "sse"} if stream else None,
            json=body,
            # Header, never `?key=`: URLs end up in logs.
            headers={"x-goog-api-key": self._api_key.get_secret_value()},
            timeout=self._timeout,
        )
        for attempt in range(self._max_attempts):
            response = await self._send_once(request, stream=stream)
            if response is None:
                raise ProviderError(self.name)
            if response.status_code == 200:
                return response
            if stream and not await self._drain(response):
                raise ProviderError(self.name)

            status = response.status_code
            logger.warning("provider_http_error", extra={"provider": self.name, "status": status})
            if status not in RETRYABLE_STATUS:
                raise ProviderError(self.name)
            retry_after = parse_retry_after(response) if status == 429 else None
            last = attempt == self._max_attempts - 1
            if status == 429 and (last or (retry_after or 0) > MAX_RETRY_WAIT_SECONDS):
                raise ProviderRateLimited(self.name, retry_after)
            if last:
                raise ProviderError(self.name)
            await self._sleep(max(backoff_delay(attempt), retry_after or 0))
        raise ProviderError(self.name)  # unreachable: the loop always returns or raises

    async def _drain(self, response: httpx.Response) -> bool:
        """Read and close a streamed error response. False if the read failed."""
        try:
            await response.aread()
        except httpx.HTTPError as exc:
            logger.warning(
                "provider_transport_error",
                extra={"provider": self.name, "error_type": type(exc).__name__},
            )
            return False
        finally:
            await response.aclose()
        return True

    async def _send_once(self, request: httpx.Request, *, stream: bool) -> httpx.Response | None:
        """One attempt. Transport errors and timeouts are not retried."""
        try:
            return await self._client.send(request, stream=stream)
        except httpx.HTTPError as exc:
            logger.warning(
                "provider_transport_error",
                extra={"provider": self.name, "error_type": type(exc).__name__},
            )
            return None

    def _candidate_text(self, data: object) -> str:
        """The answer text of the first candidate, or a typed output error."""
        if not isinstance(data, dict):
            raise self._output_error("empty")
        if _dict(data.get("promptFeedback")).get("blockReason"):
            raise self._output_error("blocked")
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise self._output_error("empty")
        candidate = _dict(candidates[0])
        finish = candidate.get("finishReason")
        if finish == "MAX_TOKENS":
            raise self._output_error("truncated")
        if finish not in (None, "STOP"):
            raise self._output_error("blocked")
        text = _part_text(candidate)
        if not text:
            raise self._output_error("empty")
        return text

    def _output_error(self, reason: OutputErrorReason) -> ProviderOutputError:
        return ProviderOutputError(self.name, reason)


_INVALID = object()


def _json_or(raw: bytes, default: object) -> object:
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):  # RecursionError: absurdly nested JSON
        return default


def _validate_or_none[T: BaseModel](model: type[T], data: object) -> T | None:
    try:
        return model.model_validate(data)
    except ValidationError:
        return None


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _part_text(candidate: dict[str, Any]) -> str:
    """Join the text parts of a candidate, skipping the model's thought parts."""
    parts = _dict(candidate.get("content")).get("parts")
    if not isinstance(parts, list):
        return ""
    return "".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and not part.get("thought") and isinstance(part.get("text"), str)
    )
