# B2 LLM provider layer + Gemini adapter — design

## Intent
One small, provider-agnostic interface the rest of the API calls to get either a validated,
typed structured answer or a stream of text deltas from an LLM — and its first adapter,
Gemini over plain httpx. Every failure mode becomes a typed error that B1's handlers already
turn into the envelope; the API key cannot leak through logs, errors or reprs. Groq and
fallback come later (B3) and reuse the same pieces.

## Goals
1. `LLMProvider` Protocol: `complete()` (structured, typed) and `stream_text()` (text deltas), with usage metadata (latency, tokens when available).
2. `GeminiProvider` using httpx and Gemini's JSON output mode, producing `SqlAnswer`.
3. Model output validated with Pydantic; invalid/non-JSON/blocked/truncated output → `ProviderOutputError`, never an unhandled exception.
4. Per-attempt timeout; bounded retries with jitter **only** for HTTP 429/5xx; `ProviderRateLimited` carrying retry-after.
5. Unit tests on `httpx.MockTransport` only; one optional `@pytest.mark.live` integration test, skipped by default.

## Non-goals
- No NL→SQL system prompt, no `/ask` endpoint, no app wiring (B3).
- No SQL safety validation (sqlglot; B3). `SqlAnswer.sql` is untrusted text here.
- No Groq adapter, no fallback orchestration.

## Anti-goals
- The key in a URL, log line, exception message/chain, `repr`, or test failure output.
- Retrying non-retryable failures (4xx, timeouts) or retrying a stream after deltas were yielded.
- Upstream response text (provider error messages, model output) reaching the client.

## Constraints
- httpx moves from dev to runtime dependency (already in the stack; no SDK — see Approaches).
- B1's 20 s request timeout bounds everything: per-attempt timeout × attempts + backoff must fit.

## Approaches
- **A. Typed Protocol + hand-written httpx adapter** — chosen by the user. One dependency we already have, full control of key placement, every path testable with `MockTransport`.
- B. `google-genai` SDK — rejected: heavy dependency tree, own retry/logging to audit, poor `MockTransport` fit.
- C. Raw dict schema in, dict out — rejected: schema and validation can drift; typed-error guarantee pushed to every caller.

## Decided by the user
- Approach A.
- `chart_hint` ∈ `table | number | bar | line | scatter` exactly; anything else is invalid output.
- Output fields `{sql, explanation, response_language ("es"|"en"), chart_hint, needs_clarification, clarification_question}`; `explanation` and `clarification_question` in the question's language.
- Retries only for 429/5xx, with jitter; typed `ProviderRateLimited` with retry-after; no network in unit tests; one optional live test.

## Facts from the current Gemini docs (checked 2026-11-25)
- Non-streaming: `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`; streaming: `…:streamGenerateContent?alt=sse`.
- Body: `systemInstruction` (`{parts:[{text}]}`), `contents` (`[{role:"user", parts:[{text}]}]`), `generationConfig.responseMimeType = "application/json"`, `generationConfig.responseJsonSchema` (JSON Schema; nullable as `anyOf` with `null` / type arrays; `minLength`/`maxLength` are silently stripped by Gemini).
- Response: `candidates[0].content.parts[].text`, `candidates[0].finishReason` (`STOP` = success; `MAX_TOKENS`, `SAFETY`, … = failure), `promptFeedback.blockReason` (no candidates), `usageMetadata.promptTokenCount` / `candidatesTokenCount`.
- Sampling params (`temperature`, `topP`, `topK`) are ignored on 3.6+ models and may 400 later → we send none. We do send `generationConfig.maxOutputTokens` (a cap, not a sampling param).
- `gemini-2.5-flash` shuts down 2026-12-01; docs recommend `gemini-3.5-flash-lite` (thinking defaults to `minimal`).
- **Not confirmed by the docs** (handled defensively, settled by the live test): `x-goog-api-key` header on `generateContent`; 429 retry-delay location; raw SSE event shape.

## Design

### Package `askdata_api/llm/`
- `base.py`
  - `Usage` (frozen dataclass): `latency_ms: float`, `input_tokens: int | None`, `output_tokens: int | None`.
  - `Completion[T: BaseModel]` (frozen dataclass): `output: T`, `usage: Usage`, `provider: str`, `model: str`.
  - `class LLMProvider(Protocol)`: `name: str`; `async def complete(self, *, system: str, user: str, output_model: type[T]) -> Completion[T]`; `def stream_text(self, *, system: str, user: str) -> AsyncIterator[str]`.
- `errors.py` (subclasses of B1's types so existing handlers map them; no new HTTP wiring)
  - B1's `ProviderError(provider)` is used as-is for transport errors, timeouts, non-retryable 4xx and exhausted 5xx retries (503 `provider_unavailable`).
  - `ProviderOutputError(ProviderError)`: adds `reason: Literal["invalid_json", "schema_mismatch", "blocked", "truncated", "empty"]` (logs only). 503 `provider_unavailable`.
  - `ProviderRateLimited(RateLimitedError)`: adds `provider: str`; `retry_after_seconds: int | None`. 429 `rate_limited` + `Retry-After`.
  - **B2 changes B1's `_handle_app_error`** (small): log `provider` via `getattr(exc, "provider", None)` (so `ProviderRateLimited` is covered too) and `state=reason` when present (both already allowlisted log fields). B1's tests stay green; one new test covers it.
- `answer.py`
  - `ChartHint = Literal["table", "number", "bar", "line", "scatter"]`.
  - `SqlAnswer(BaseModel)`, `extra="forbid"`: `sql: str | None` (max 4000), `explanation: str` (1..2000), `response_language: Literal["es", "en"]`, `chart_hint: ChartHint`, `needs_clarification: bool`, `clarification_question: str | None` (max 500).
  - Model validator: `needs_clarification=True` ⇒ non-empty `clarification_question` and `sql is None`; `False` ⇒ non-empty `sql` and `clarification_question is None`.
  - Literals (not enums) so the generated JSON Schema has inline `enum` and no `$defs`.
- `retry.py`
  - `RETRYABLE_STATUS = {429, 500, 502, 503, 504}`.
  - `backoff_delay(attempt: int, *, base: float = 0.5, cap: float = 4.0, rand=random.uniform) -> float` — full jitter: `rand(0, min(cap, base * 2**attempt))`.
  - `parse_retry_after(response: httpx.Response) -> int | None` — `Retry-After` header (seconds) first, else a `google.rpc.RetryInfo` `retryDelay` like `"30s"`/`"1.5s"` in `error.details[]`; rounds up; ignores malformed values.
  - The retry loop itself lives in the adapter (one caller today; Groq will reuse `backoff_delay`/`parse_retry_after`).
- `gemini.py` — `GeminiProvider`
  - `__init__(self, *, api_key: SecretStr, model: str, client: httpx.AsyncClient, timeout_seconds: float = 5.0, max_attempts: int = 3, max_output_tokens: int = 2048, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep)`; `name = "gemini"`. Key kept as `SecretStr`; `get_secret_value()` called only when building the request headers. `__repr__` shows provider and model only.
  - Key sent as `x-goog-api-key` header — never `?key=` (URLs end up in logs).
  - `complete()`: POST `generateContent` with the body above and `responseJsonSchema = output_model.model_json_schema()`. Up to `max_attempts`: 429/5xx → retry after `max(backoff_delay(attempt), retry_after or 0)`; on 429 whose retry-after exceeds `cap` (4 s), stop immediately (fail fast, let a future fallback act) → `ProviderRateLimited`. Exhausted 429 → `ProviderRateLimited`; exhausted 5xx → `ProviderError`. Other 4xx, `httpx.TimeoutException`, `httpx.TransportError` → `ProviderError`, no retry. Then parse: `blockReason` → `blocked`; no candidate/parts → `empty`; `finishReason` `MAX_TOKENS` → `truncated`, other non-`STOP` → `blocked`; join non-`thought` part texts; `json.loads` failure → `invalid_json`; `output_model.model_validate` failure → `schema_mismatch`.
  - Budget: 5 s per attempt × 3 attempts + backoff (≤ 4 s each, ~1 s typical) ≈ 17–23 s worst case; B1's 20 s request timeout is the hard bound and cuts the rare tail.
  - `stream_text()`: POST `streamGenerateContent?alt=sse`; same status handling and retries **only before the first delta is yielded**; read `aiter_lines()`, for each `data: ` line `json.loads` the payload and yield the joined non-thought part texts (skip empty). Failures: `blockReason` or bad JSON in an event → `ProviderOutputError` (`blocked` / `invalid_json`); the last seen `finishReason` is checked when the stream ends — `MAX_TOKENS` → `truncated`, any other non-`STOP` → `blocked`, none and no text at all → `empty`; a transport error or read timeout mid-stream → `ProviderError`. Yields `str` only: the user asked for text deltas; no usage is returned from streams (add later if a caller needs it).
  - Timeouts: one mechanism for both methods — `httpx.Timeout(timeout_seconds)` passed per request (connect/read/write/pool). For `complete()` that bounds the wait for the single response; for `stream_text()` it bounds the gap between chunks, so long answers are not cut while a stalled stream is.
  - Every raised error uses `from None` so the httpx exception chain (request, response, URL) is not attached; messages are fixed literals; no response body, status text or model output is ever put in an exception or log.
- `settings.py`: add `gemini_model: str = "gemini-3.5-flash-lite"` (env `GEMINI_MODEL`), validated `^[a-z0-9][a-z0-9.-]{0,63}$` because it is interpolated into the URL path.
- `pyproject.toml`: httpx to runtime deps; register `live` marker; `addopts` gains `-m "not live"`.

### Key-leak controls (the "redact Authorization in debug output" requirement)
No code path in our package prints request headers, so there is no debug output to redact. This is enforced, not assumed:
- a test runs every failure scenario with **all loggers at DEBUG** (incl. httpx, httpcore) and asserts the key is absent from captured log text;
- the same scenarios assert the key is absent from `str(exc)`, `repr(exc)`, the whole `__cause__`/`__context__` chain and `repr(provider)`;
- B1's JSON formatter already redacts third-party messages.
(httpx's own `Headers` repr masks only `authorization`, not `x-goog-api-key` — another reason never to log headers.)

### Tests (`tests/llm/`)
MockTransport handler records requests and returns scripted responses; `sleep` is a no-op recorder.
- Request shape: URL path/model, `alt=sse` for streaming, `x-goog-api-key` header present and key absent from URL, `responseMimeType`, `responseJsonSchema` has `enum` for `chart_hint` and `response_language`, `maxOutputTokens` 2048, no sampling params.
- Happy path ES and EN (question fixtures in both languages; `response_language` `es`/`en`, explanation in that language) → `Completion` with output, tokens from `usageMetadata`, `latency_ms ≥ 0`, provider `gemini`, model id.
- `needs_clarification` ES and EN with `sql: null`.
- Output errors, each → `ProviderOutputError(reason)`: non-JSON text; JSON with `chart_hint: "pie"`; extra field; clarification invariant broken; `blockReason`; `finishReason: MAX_TOKENS`; `SAFETY`; no candidates.
- Retries: 503,503,200 → success after 2 sleeps; 500×3 → `ProviderError`, 2 sleeps; 429 with `Retry-After: 2` then 200 → slept ≥ 2; 429 `RetryInfo "30s"` → immediate `ProviderRateLimited(retry_after=30)`, no sleep; 429×3 small delay → `ProviderRateLimited`; 400/401/403/404 → `ProviderError`, exactly 1 request; `httpx.ReadTimeout` → `ProviderError`, 1 request.
- `backoff_delay` bounds; `parse_retry_after` header/RetryInfo/malformed.
- Streaming: SSE with three deltas (ES and EN) → yielded in order; 503 then stream → retried; bad JSON after first delta → `ProviderOutputError`, no retry; transport error mid-stream → `ProviderError`; `blockReason` in an event → `blocked`; final `finishReason: MAX_TOKENS` → `truncated`; `SAFETY` → `blocked`.
- Key-leak test described above; `repr(GeminiProvider)`.
- Settings: `GEMINI_MODEL` default and rejected values (`../x`, `a/b`, `A`, empty).
- Handler logging: `ProviderRateLimited` and `ProviderOutputError` log records carry `provider` and (output error) `state=reason`.
- Envelope integration: route raising `ProviderOutputError` → 503 `provider_unavailable` (no reason in body); `ProviderRateLimited(7)` → 429 + `Retry-After: 7`.
- Live (`@pytest.mark.live`, needs `GEMINI_API_KEY`): one Spanish and one English question → valid `SqlAnswer` with matching `response_language`; one `stream_text` call yields ≥ 1 delta. Run: `uv run pytest -m live`.

## Builder's calls (Claude)
- `complete()` takes the Pydantic class instead of a raw schema (schema generated from it; single source of truth).
- Output-error subclasses reuse 503 `provider_unavailable` — the client shows the same "try again" either way; `reason` is for logs.
- Upstream 429 maps to client 429 with `Retry-After` (honest until fallback exists).
- Timeouts are not retried (they would eat the request budget), per "retries only for 429/5xx".
- Default model `gemini-3.5-flash-lite`, overridable by `GEMINI_MODEL`; no sampling or thinking params.
- `maxOutputTokens=2048` per request (constructor param): bounds quota use per call (THREAT_MODEL A6) and comfortably fits `SqlAnswer`'s caps (~6500 chars), thinking included.
- No overall deadline parameter yet: B3's fallback can pass a smaller `max_attempts`/`timeout_seconds`; adding a keyword-only `deadline` later does not break callers.
- Live test reads `GEMINI_API_KEY` at module import (the autouse env-isolation fixture deletes it per test); `GEMINI_MODEL` is added to that fixture's list.
- Length caps in `SqlAnswer` are enforced by Pydantic (Gemini strips them from the schema).

## Left to the builder
Fixture/helper shapes, exact test file split, internal helper names in `gemini.py`.
