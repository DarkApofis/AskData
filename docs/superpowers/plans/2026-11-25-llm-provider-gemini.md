# B2 LLM Provider + Gemini Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A typed `LLMProvider` Protocol and a `GeminiProvider` over httpx that returns a validated `SqlAnswer` or streams text, with typed errors, bounded retries and no key leakage.

**Architecture:** `askdata_api/llm/` package: `base.py` (Protocol, `Completion`, `Usage`), `errors.py` (subclasses of B1's `ProviderError`/`RateLimitedError`), `answer.py` (`SqlAnswer`), `retry.py` (pure helpers), `gemini.py` (adapter). The adapter receives an `httpx.AsyncClient`, so tests inject `httpx.MockTransport`.

**Tech Stack:** Python 3.12, httpx, Pydantic v2, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-11-25-llm-provider-gemini-design.md`

## Global Constraints
- Base branch `feat/backend-foundations` (B1 unmerged); work on `feat/llm-gemini`.
- Endpoint `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`, streaming `…:streamGenerateContent?alt=sse`; key only in header `x-goog-api-key`.
- `generationConfig`: `responseMimeType: "application/json"`, `responseJsonSchema`, `maxOutputTokens: 2048`; no `temperature`/`topP`/`topK`/`thinkingConfig`.
- `ChartHint = Literal["table","number","bar","line","scatter"]`; `response_language: Literal["es","en"]`.
- `RETRYABLE_STATUS = {429, 500, 502, 503, 504}`; defaults `timeout_seconds=5.0`, `max_attempts=3`, backoff `base=0.5`, `cap=4.0`; 429 with retry-after > 4 → immediate `ProviderRateLimited`.
- Every raised error: fixed message, `from None`, never response body / model output / key.
- `ProviderOutputError.reason ∈ {"invalid_json","schema_mismatch","blocked","truncated","empty"}`.
- No network in unit tests; live test only with `-m live`. Question fixtures exist in es and en.
- New runtime dependency: httpx only (moved from dev).

## Review Focus
1. HTTP 200 whose body is not Gemini JSON (proxy HTML page) → `ProviderOutputError("invalid_json")`, not a crash. Test in Task 3.
2. JSON answer split across several `parts` → texts joined before parsing. Test in Task 3.
3. A `"thought": true` part with prose before the answer → skipped, answer still parses. Test in Task 3.
4. SSE stream with blank keep-alive lines, `: comment` lines, CRLF endings, `event:` lines → ignored; only `data:` parsed. Test in Task 4.
5. `Retry-After` as HTTP-date, negative, or garbage → `None` (normal backoff), never an exception. Test in Task 2.

---

### Task 1: Contracts — settings, answer model, errors, Protocol

**Files:** Modify `apps/api/pyproject.toml` (httpx → `[project].dependencies`; marker `live`; `addopts` add `-m "not live"`), `src/askdata_api/settings.py`, `src/askdata_api/errors.py` (`_handle_app_error`), `tests/conftest.py` (add `GEMINI_MODEL` to isolated vars), `.env.example` (`# GEMINI_MODEL=gemini-3.5-flash-lite`). Create `src/askdata_api/llm/__init__.py`, `llm/base.py`, `llm/errors.py`, `llm/answer.py`, `tests/llm/__init__.py`, `tests/llm/test_contracts.py`.

**Interfaces — Produces:**
- `Settings.gemini_model: str` default `"gemini-3.5-flash-lite"`, must match `^[a-z0-9][a-z0-9.-]{0,63}$`.
- `Usage(latency_ms: float, input_tokens: int | None, output_tokens: int | None)`, `Completion[T](output: T, usage: Usage, provider: str, model: str)` — frozen dataclasses.
- `class LLMProvider(Protocol)`: `name: str`; `async def complete(self, *, system: str, user: str, output_model: type[T]) -> Completion[T]`; `def stream_text(self, *, system: str, user: str) -> AsyncIterator[str]`.
- `ProviderOutputError(ProviderError)(provider: str, reason: OutputErrorReason)`; `ProviderRateLimited(RateLimitedError)(provider: str, retry_after_seconds: int | None)`; `OutputErrorReason` Literal alias.
- `ChartHint`, `SqlAnswer` per spec (caps: sql ≤ 4000, explanation 1..2000, clarification ≤ 500; `extra="forbid"`; clarification invariant).

- [ ] Tests: `test_gemini_model_default`, `test_gemini_model_rejects` (`"../x"`, `"a/b"`, `"A"`, `""`, `"x"*65`); `test_sql_answer_valid_es`/`_en`; `test_sql_answer_clarification_es`/`_en` (`sql=None`); `test_sql_answer_rejects` (chart `"pie"`, language `"fr"`, extra field, clarification without question, clarification with sql, answer without sql, empty explanation, sql > 4000); `test_schema_has_inline_enums` (`SqlAnswer.model_json_schema()` has no `$defs`, `chart_hint` enum == 5 values); `test_output_error_is_503_without_reason` and `test_provider_rate_limited_is_429_with_retry_after` (route raising them → envelope, `Retry-After: 7`, body has no `reason`/provider text); `test_handler_logs_provider_and_reason` (caplog record `app_error` has `provider="gemini"`, `state="schema_mismatch"`; rate-limited has `provider`).
- [ ] Run → FAIL. Implement. `uv run pytest -q && uv run ruff check . && uv run mypy` → pass. Commit `feat(api): LLM provider contracts and SqlAnswer model`.

### Task 2: Retry helpers

**Files:** Create `src/askdata_api/llm/retry.py`, `tests/llm/test_retry.py`.

**Interfaces — Produces:** `RETRYABLE_STATUS: frozenset[int]`; `backoff_delay(attempt: int, *, base: float = 0.5, cap: float = 4.0, rand: Callable[[float, float], float] = random.uniform) -> float`; `parse_retry_after(response: httpx.Response) -> int | None`.

- [ ] Tests: `test_backoff_is_full_jitter_and_capped` (rand records its bounds: attempt 0 → (0, 0.5), 1 → (0, 1.0), 10 → (0, 4.0)); `test_retry_after_header_seconds` (`"2"` → 2); `test_retry_after_from_retry_info` (body `{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","details":[{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"30s"}]}}` → 30; `"1.5s"` → 2); `test_header_wins_over_body`; `test_retry_after_garbage_is_none` (header `"Wed, 21 Oct 2026 07:28:00 GMT"`, `"-3"`, `"abc"`, body not JSON, `retryDelay: "soon"`).
- [ ] Run → FAIL. Implement. Suite + ruff + mypy → pass. Commit `feat(api): retry helpers for LLM providers`.

### Task 3: `GeminiProvider.complete()`

**Files:** Create `src/askdata_api/llm/gemini.py`, `tests/llm/fakes.py` (scripted MockTransport + no-op sleep recorder + Gemini response builders), `tests/llm/test_gemini_complete.py`, `tests/llm/test_gemini_secrets.py`.

**Interfaces — Consumes:** Task 1 types, Task 2 helpers. **Produces:** `GeminiProvider(*, api_key: SecretStr, model: str, client: httpx.AsyncClient, timeout_seconds: float = 5.0, max_attempts: int = 3, max_output_tokens: int = 2048, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep)`; `name = "gemini"`; `GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"`.

- [ ] Tests (`test_gemini_complete.py`), question fixtures `QUESTIONS = {"es": "¿Cuántas ventas hubo por región en 2025?", "en": "How many sales were there per region in 2025?"}`:
  - `test_request_shape` (path `/v1beta/models/gemini-3.5-flash-lite:generateContent`, header key, no `key` query param, systemInstruction/contents text, `responseMimeType`, `responseJsonSchema == SqlAnswer.model_json_schema()`, `maxOutputTokens == 2048`, none of temperature/topP/topK/thinkingConfig).
  - `test_complete_success[es|en]` (output fields, `response_language`, tokens 120/45 from usageMetadata, provider/model, latency ≥ 0).
  - `test_complete_clarification[es|en]`.
  - `test_output_errors` parametrized → reason: non-JSON text → invalid_json; envelope body `<html>` with 200 → invalid_json; `chart_hint "pie"` → schema_mismatch; extra field → schema_mismatch; `promptFeedback.blockReason` → blocked; `finishReason MAX_TOKENS` → truncated; `SAFETY` → blocked; no candidates → empty; candidate without parts → empty.
  - `test_parts_are_joined` (JSON split over 2 parts); `test_thought_parts_are_skipped`.
  - Retries: `test_retries_5xx_then_succeeds` (503, 503, 200 → 3 requests, 2 sleeps); `test_5xx_exhausted_is_provider_error` (500×3 → `ProviderError`, not `ProviderOutputError`, 2 sleeps); `test_429_waits_retry_after_then_succeeds` (Retry-After 2 → slept ≥ 2); `test_429_long_retry_after_fails_fast` (RetryInfo 30s → `ProviderRateLimited(retry_after_seconds=30)`, 1 request, 0 sleeps); `test_429_exhausted` (Retry-After 1 ×3 → `ProviderRateLimited(1)`); `test_4xx_not_retried` (400/401/403/404 → `ProviderError`, 1 request); `test_timeout_not_retried` (handler raises `httpx.ReadTimeout` → `ProviderError`, 1 request); `test_connect_error_not_retried`.
- [ ] Tests (`test_gemini_secrets.py`), key `"AIzaSyTEST-not-a-real-key-000"` (`# gitleaks:allow`): `test_key_never_leaks` parametrized over every failure scenario above with `caplog.set_level(logging.DEBUG)` on root, `httpx`, `httpcore` → key absent from `caplog.text`, `str(exc)`, `repr(exc)`, every exception in the `__cause__`/`__context__` chain; `exc.__cause__ is None`; `test_repr_hides_key`.
- [ ] Run → FAIL. Implement. Suite + ruff + mypy → pass. Commit `feat(api): Gemini provider structured completion`.

### Task 4: `GeminiProvider.stream_text()`

**Files:** Modify `src/askdata_api/llm/gemini.py`. Create `tests/llm/test_gemini_stream.py`.

- [ ] Tests (SSE bodies built by `fakes.py`):
  - `test_stream_request_shape` (path `:streamGenerateContent`, `alt=sse`, header key, no `responseJsonSchema`, `maxOutputTokens` 2048).
  - `test_stream_yields_deltas_in_order[es|en]` (3 events → 3 strings).
  - `test_stream_ignores_non_data_lines` (blank, `: ping`, `event: x`, CRLF).
  - `test_stream_retries_before_first_delta` (503 then stream → deltas, 1 sleep).
  - `test_stream_bad_json_after_delta` (`ProviderOutputError("invalid_json")` after first delta yielded, 1 request).
  - `test_stream_transport_error_mid_stream_is_provider_error`.
  - `test_stream_block_reason` → blocked; `test_stream_final_max_tokens` → truncated; `test_stream_final_safety` → blocked; `test_stream_empty` (no text, no finishReason) → empty.
  - Key-leak test extended to two streaming failures.
- [ ] Run → FAIL. Implement. Suite + ruff + mypy → pass. Commit `feat(api): Gemini provider text streaming`.

### Task 5: Live test, docs, CI

**Files:** Create `tests/llm/test_gemini_live.py`. Modify `docs/THREAT_MODEL.md` (A6: output token cap + fail-fast 429 in place; A8: key header-only + leak test; A2: note model output is untrusted until B3 validation), `docs/BACKLOG.md` (Groq adapter + fallback; stream usage metadata; overall deadline param), `README.md` (`uv run pytest -m live` with `GEMINI_API_KEY`).

- [ ] `_LIVE_KEY = os.environ.get("GEMINI_API_KEY")` read at module import; `pytestmark = [pytest.mark.live, pytest.mark.skipif(not _LIVE_KEY, reason="GEMINI_API_KEY not set")]`. Tests: `test_live_complete[es|en]` (minimal system prompt asking for a `SqlAnswer` over a 2-column schema; asserts `response_language` matches), `test_live_stream` (≥ 1 delta).
- [ ] `uv run pytest -q` → live tests deselected (count shown in summary); `uv run pytest -m live -q` without key → skipped.
- [ ] Commit `test(api): optional live Gemini test; docs`. Push `feat/llm-gemini`; CI green.
