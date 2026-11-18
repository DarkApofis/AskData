# B1 Backend Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fail-closed settings, one error envelope, metadata-only JSON logs with request IDs, strict CORS, security headers, body-size limit and request timeout for `apps/api`.

**Architecture:** Pure ASGI middleware, one module per concern, assembled in `create_app(settings)`; FastAPI exception handlers and every middleware build error bodies through one `error_response()`; a `ContextVar` carries the request ID to logs and error bodies.

**Tech Stack:** FastAPI, Starlette, Pydantic v2, pydantic-settings, stdlib logging, pytest + pytest-asyncio + httpx `ASGITransport`.

**Spec:** `docs/superpowers/specs/2026-11-17-backend-foundations-design.md`

## Global Constraints
- Error body is exactly `{"code", "message", "request_id"}`; messages are the spec's English table, never caller/provider text.
- Codes/status: `validation_error` 422, `payload_too_large` 413, `rate_limited` 429, `provider_unavailable` 503, `request_timeout` 504, `not_found` 404, `method_not_allowed` 405, `cors_rejected` 403, `internal_error` 500.
- Middleware order, outermost first: SecurityHeaders → RequestId → StrictCORS → ErrorBoundary → BodySizeLimit → Timeout.
- Settings env-only (`env_file=None`), `hide_input_in_errors=True`; both LLM keys required; `APP_ENV` default `production`; `MAX_BODY_BYTES` 65536; `REQUEST_TIMEOUT_SECONDS` 20.
- Logs: allowlisted extras only (`method, path, status, latency_ms, code, provider, state, error_type, frames`); never question text, rows, SQL, settings values, exception messages.
- Only new dependency: `pydantic-settings`. mypy --strict and ruff stay clean.

## Review Focus
1. Exception raised after the response has started (streaming) → boundary must not send a second `http.response.start`; connection just ends. Test in Task 4.
2. Lying `Content-Length` (declares 10, streams 70 000) → streamed-byte counter still returns 413. Test in Task 5.
3. Timeout fires while handler still running → exactly one 504, and the late handler's sends are ignored without an `internal_error` log. Test in Task 5.
4. `ALLOWED_ORIGINS="https://a.com, https://b.com"` (spaces) is trimmed; `"https://a.com,,"` (empty entry) is rejected. Test in Task 1.
5. Non-preflight `OPTIONS /healthz` with no `Origin` → 405 envelope (not swallowed by CORS). Test in Task 4.

---

### Task 1: Settings

**Files:** Create `src/askdata_api/settings.py`, `tests/conftest.py`, `tests/test_settings.py`. Modify `src/askdata_api/main.py`, `tests/test_health.py`, `pyproject.toml` (add `pydantic-settings`), `.env.example` (add `APP_ENV`, `MAX_BODY_BYTES`, `REQUEST_TIMEOUT_SECONDS`, `LOG_LEVEL` with defaults commented), `README.md` (run with `uv run --env-file .env uvicorn ...`).

**Interfaces:** Produces `Settings` (fields per spec table; `allowed_origins: list[str]`), `create_app(settings: Settings | None = None) -> FastAPI`, test helper fixture `settings` (development, origins `["http://localhost:3000", "https://askdata.example"]`, fake keys `"gem-test-key-123"`, `"groq-test-key-456"`) and fixture `client` built from `create_app(settings)`; `make_settings(**overrides: object) -> Settings` in `tests/conftest.py` (passes the fixture defaults merged with overrides as init kwargs, so tests never depend on the real environment).

- [ ] Tests: `test_missing_groq_key_fails` (`ValidationError` mentions `groq_api_key`), `test_validation_error_hides_present_secret` (`"gem-test-key-123"` not in `str(err)`), `test_repr_hides_secrets`, `test_origins_reject_wildcard`, `test_origins_reject_path_and_trailing_slash`, `test_origins_trim_spaces`, `test_origins_reject_empty_entry`, `test_production_requires_https`, `test_development_allows_localhost_http`, `test_app_env_defaults_to_production`, `test_import_main_without_env_fails` (subprocess `python -c "import askdata_api.main"` with `GEMINI_API_KEY` removed and `GROQ_API_KEY` set to the fake `GROQ_TEST_KEY` → non-zero exit, stderr contains `gemini_api_key` and not `groq-test-key-456`).
- [ ] Run `uv run pytest tests/test_settings.py` → FAIL (no module).
- [ ] Implement; `create_app` builds `Settings()` when `None`. Env parsing: `Annotated[list[str], NoDecode]` + `field_validator(mode="before")` splitting on `,`.
- [ ] `uv run pytest && uv run ruff check . && uv run mypy` → all pass.
- [ ] Commit `feat(api): typed env-only settings that fail fast`.

### Task 2: Error model and handlers

**Files:** Create `src/askdata_api/request_context.py`, `src/askdata_api/errors.py`, `tests/test_errors.py`. Modify `main.py` (call `register_error_handlers`).

**Interfaces:** Produces `request_id_var: ContextVar[str]` (default `"unknown"`); `ErrorCode(StrEnum)` with `.status: int` and `.message: str`; `ErrorResponse`; `AppError(code)`, `RateLimitedError(retry_after_seconds: int | None = None)`, `ProviderError(provider: str)`; `error_response(code: ErrorCode, headers: Mapping[str, str] | None = None) -> JSONResponse` (reads `request_id_var`); `register_error_handlers(app: FastAPI) -> None`.

- [ ] Tests (test-only routes added to the fixture app): `test_provider_error_hides_upstream_message` (raise `ProviderError("gemini")` from `raise ... from RuntimeError("quota exceeded for key AIza...")` → 503, body exactly 3 keys, no `quota`/`AIza`), `test_rate_limited_sets_retry_after` (429, `Retry-After: 30`), `test_404_uses_envelope`, `test_405_uses_envelope`, `test_invalid_json_is_422_without_echo` (POST `{"q": "SECRET_MARKER"` malformed → 422 `validation_error`, `SECRET_MARKER` not in body), `test_every_code_has_status_and_message`.
- [ ] Run → FAIL. Implement. Run full suite + ruff + mypy → pass. Commit `feat(api): single error envelope with stable codes`.

### Task 3: JSON logging

**Files:** Create `src/askdata_api/logging_setup.py`, `tests/test_logging.py`. Modify `main.py` (call `configure_logging(settings.log_level)`).

**Interfaces:** Consumes `request_id_var`. Produces `ALLOWED_LOG_FIELDS: frozenset[str]`, `JsonFormatter(logging.Formatter)`, `configure_logging(level: str) -> None` (root handler to stdout with `JsonFormatter`; uvicorn/uvicorn.error propagate to root with no own handlers; `uvicorn.access` disabled).

- [ ] Tests: `test_formatter_emits_json_with_request_id` (set var, format a record → `json.loads` has `ts, level, logger, event, request_id`), `test_formatter_drops_unknown_extras` (`extra={"question": "¿cuántas ventas?", "status": 200}` → `status` kept, `question` absent), `test_formatter_omits_exception_message` (record with `exc_info` from `ValueError("SECRET_SQL")` → `SECRET_SQL` not in output, `error_type == "ValueError"`).
- [ ] Run → FAIL. Implement. Suite + ruff + mypy → pass. Commit `feat(api): metadata-only JSON logging`.

### Task 4: Request ID, security headers, strict CORS, error boundary

**Files:** Create `src/askdata_api/middleware/__init__.py`, `request_id.py`, `security_headers.py`, `cors.py`, `error_boundary.py`; `tests/test_middleware_edge.py` (request ID, headers, boundary), `tests/test_cors.py`. Modify `main.py` (add middleware in the Global order).

**Interfaces:** Consumes `request_id_var`, `error_response`, `ErrorCode`, logger. Produces `RequestIdMiddleware(app)`, `SecurityHeadersMiddleware(app)`, `StrictCORSMiddleware(app, allowed_origins: list[str])`, `ErrorBoundaryMiddleware(app)` — all pure ASGI `__call__(scope, receive, send)`, pass-through for non-`http` scopes. Access log event name `"request"`; boundary event `"unhandled_error"`.

- [ ] Tests:
  - `test_security_headers_on_200_404_500` (all three headers on each).
  - `test_unhandled_error_is_500_without_traceback` (route raises `RuntimeError("SECRET_MARKER")` → 500 `internal_error`, body keys exactly 3, no `Traceback`/`SECRET_MARKER` in body; `caplog` record has `error_type="RuntimeError"` and no `SECRET_MARKER`).
  - `test_error_after_response_started_does_not_send_twice` (StreamingResponse yielding one chunk then raising → client gets 200 start, no exception escapes the transport other than the stream ending).
  - `test_valid_request_id_is_echoed` (`X-Request-ID: abcd1234-ok` in header and in a 404 body's `request_id`).
  - `test_malicious_request_id_is_replaced` (`"x\nINJECT"` and 65-char value → response ID is 32 hex chars).
  - `test_access_log_has_metadata_only` (POST body containing `"¿Cuál es el total? / What is the total?"` → access record has method/path/status/latency_ms, message text not in any captured record).
  - `test_options_without_origin_is_405_envelope`.
  - CORS: `test_preflight_allowed_origin`, `test_preflight_disallowed_origin_is_403_without_cors_headers`, `test_simple_get_disallowed_origin_has_no_cors_headers`, `test_preflight_put_rejected`, `test_retry_after_is_exposed`.
- [ ] Run → FAIL. Implement (CORS subclass overrides `preflight_response` only for the failure branch). Suite + ruff + mypy → pass. Commit `feat(api): request id, security headers, strict CORS, error boundary`.

### Task 5: Body size limit and timeout

**Files:** Create `src/askdata_api/middleware/body_limit.py`, `timeout.py`, `tests/test_limits.py`. Modify `main.py`.

**Interfaces:** Consumes `error_response`, `ErrorCode`. Produces `BodySizeLimitMiddleware(app, max_bytes: int)`, `TimeoutMiddleware(app, seconds: float)`. Body overflow while streaming raises `starlette.exceptions.HTTPException(413)`; timeout event name `"request_timeout"`.

- [ ] Tests (test route `POST /echo-size` reading `await request.body()` and returning its length; `GET /slow` sleeping 1 s with `request_timeout_seconds=0.05`):
  - `test_content_length_over_limit_is_413` (65 537 bytes).
  - `test_body_at_limit_is_accepted` (65 536 → 200).
  - `test_chunked_body_over_limit_is_413` (async generator body, no Content-Length).
  - `test_lying_content_length_is_413` (header `10`, 70 000 streamed bytes).
  - `test_non_numeric_content_length_is_413`.
  - `test_slow_request_is_504` (`request_timeout`, security headers present, no `unhandled_error` record).
- [ ] Run → FAIL. Implement. Suite + ruff + format --check + mypy → pass.
- [ ] Update `docs/THREAT_MODEL.md` (A5, A8 status; new rows for request-ID log injection and CORS), `docs/BACKLOG.md` (rate limiter; 422 field details; `/docs` in production stays). Commit `feat(api): request body limit and timeout`.
- [ ] Push branch, CI green.
