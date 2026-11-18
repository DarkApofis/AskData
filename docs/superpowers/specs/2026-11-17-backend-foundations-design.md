# B1 Backend foundations — design

## Intent
Give `apps/api` a fail-closed base before any LLM code lands: typed settings that refuse
to start when misconfigured, one error shape that never leaks internals, metadata-only
structured logs with a request ID, strict CORS, security headers, and hard limits on
request size and duration. Every later feature (LLM providers, SQL validation, rate
limiting) plugs into these pieces instead of inventing its own.

## Goals
1. **Settings** (pydantic-settings): env-only, secrets as `SecretStr`, startup fails fast on a missing or invalid variable, settings values never logged.
2. **Single error model**: every non-2xx response body is `{code, message, request_id}`. `code` is a stable machine key the client localizes (es/en); `message` is an English fallback. Validation, rate-limit, provider, timeout, size, routing and internal errors all map to it with no stack traces or provider messages.
3. **Request ID + structured JSON logs** carrying metadata only.
4. **Strict CORS** from an env allowlist.
5. **Security headers** on every response.
6. **Body size limit** (64 KB) and **per-request timeout**.

## Non-goals
- No rate limiter (only the error type and mapping; limiter goes to BACKLOG).
- No LLM provider code (only the provider error type and mapping).
- No `/docs` hardening, CSP, HSTS (BACKLOG).
- No client-side localization (web task).

## Anti-goals
- A secret appearing in any log line, error body or exception message — including pydantic's own validation errors.
- A response body containing a traceback, exception message or upstream provider text.
- A body limit that can be bypassed by omitting `Content-Length` (chunked transfer).
- `Access-Control-Allow-Origin: *` or a reflected arbitrary origin.

## Constraints
Python 3.12, FastAPI, Pydantic v2. New dependency: **pydantic-settings** (requested; typed env parsing with `SecretStr`). No other new runtime dependency — JSON logging uses stdlib `logging`.

## Decided by the user
- Approach **A**: plain (pure) ASGI middleware per concern + FastAPI exception handlers; CORS via Starlette `CORSMiddleware`. (B `BaseHTTPMiddleware` rejected: cannot cut off a streaming body, contextvar issues. C platform limits rejected: not configurable on free tier, not testable.)
- **Both** `GEMINI_API_KEY` and `GROQ_API_KEY` are required at startup.
- CORS allowlist covers production plus localhost only; Vercel preview deployments cannot call the API (no origin patterns).
- Error model fields exactly `code`, `message`, `request_id`.
- Body limit 64 KB; tests named in the request (missing env fails startup, no stack traces, oversize → 413, disallowed origin → no CORS headers).

## Threat model for this feature
Assets: LLM keys, logs, API availability. Attackers: anonymous scripted client; malicious page in a victim's browser (cross-origin).

| Abuse case | Mitigation in B1 |
|---|---|
| Key leaks via startup error (pydantic `input_value` echoes the whole env dict) | `hide_input_in_errors=True`; test asserts a present key value never appears in the error text |
| Key leaks via `repr`/log of settings | `SecretStr`; settings never passed to a logger; test on `repr` |
| Stack trace / exception message in response | catch-all error boundary returns fixed `internal_error` body; test |
| Provider message reflected to client | `ProviderError` carries provider name only; response message fixed |
| Log injection via `X-Request-ID` | accept only `^[A-Za-z0-9._-]{8,64}$`, else generate |
| User content in logs | JSON formatter emits only an allowlist of extra fields; anything else is dropped |
| Memory/CPU DoS via huge or chunked body | 64 KB limit on declared `Content-Length` *and* on streamed bytes |
| Slowloris-style slow handlers | per-request timeout → 504 |
| Cross-origin abuse from arbitrary sites | exact-origin allowlist, `*` rejected at startup, methods GET/POST only, no credentials |
| MIME sniffing / caching of API data | `nosniff`, `no-store`, `Referrer-Policy: no-referrer` |

These rows are folded into `docs/THREAT_MODEL.md` (A5, A8 and new rows).

## Design

### Settings — `askdata_api/settings.py`
`class Settings(BaseSettings)`, `model_config = SettingsConfigDict(env_file=None, case_sensitive=False, extra="ignore", hide_input_in_errors=True, frozen=True)`.

| Env var | Field | Type / rule | Default |
|---|---|---|---|
| `GEMINI_API_KEY` | `gemini_api_key` | `SecretStr`, non-empty | required |
| `GROQ_API_KEY` | `groq_api_key` | `SecretStr`, non-empty | required |
| `ALLOWED_ORIGINS` | `allowed_origins` | comma-separated, ≥1; each must be scheme+host[+port], no path/query/trailing slash, no `*`; `https` required when `APP_ENV=production` (`http://localhost` / `http://127.0.0.1` allowed otherwise) | required |
| `APP_ENV` | `app_env` | `Literal["production", "development"]` | `production` (fail closed) |
| `MAX_BODY_BYTES` | `max_body_bytes` | int, 1..1_048_576 | `65536` |
| `REQUEST_TIMEOUT_SECONDS` | `request_timeout_seconds` | float, >0, ≤60 | `20` |
| `LOG_LEVEL` | `log_level` | `Literal["DEBUG","INFO","WARNING","ERROR"]` | `INFO` |

- Comma list parsed with `Annotated[list[str], NoDecode]` + a `field_validator` (pydantic-settings otherwise expects JSON).
- Env-only: local dev loads `.env` via `uv run --env-file .env …` (documented in README), not by the app.
- Fail fast: `create_app(settings: Settings | None = None)`; when `None` it builds `Settings()`. `main.py` keeps `app = create_app()` at module level, so a bad env aborts `uvicorn` import before it listens. The resulting `ValidationError` names fields and error types only.

### Errors — `askdata_api/errors.py`
- `class ErrorCode(StrEnum)` with the stable codes and their HTTP status:

| code | status | English message |
|---|---|---|
| `validation_error` | 422 | The request is invalid. |
| `payload_too_large` | 413 | The request body is too large. |
| `rate_limited` | 429 | Too many requests. Try again later. |
| `provider_unavailable` | 503 | The language model is unavailable. Try again later. |
| `request_timeout` | 504 | The request took too long. |
| `not_found` | 404 | Not found. |
| `method_not_allowed` | 405 | Method not allowed. |
| `cors_rejected` | 403 | Origin or method not allowed. |
| `internal_error` | 500 | Something went wrong. |

- `class ErrorResponse(BaseModel)`: `code: ErrorCode`, `message: str`, `request_id: str`.
- `class AppError(Exception)`: `code: ErrorCode`; message always the table's (no caller-supplied text reaches the client). Subclasses: `RateLimitedError(retry_after_seconds: int | None)` (adds `Retry-After` header), `ProviderError(provider: str)` (provider name is for logs only).
- `error_response(code, request_id, headers=None) -> JSONResponse` — the one function that builds an error body; middlewares and handlers both use it.
- Handlers registered by `register_error_handlers(app)`: `AppError`; `RequestValidationError` → `validation_error` (no field details, no input echo — details to BACKLOG); `StarletteHTTPException` → by status (404, 405, 413; any other 4xx → `validation_error`, 5xx → `internal_error`).

### Middleware — `askdata_api/middleware/` (one module per concern, pure ASGI)
Order, outermost first (Starlette's own `ServerErrorMiddleware` stays outside all of them):

1. `SecurityHeadersMiddleware` — sets `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` on every response, including errors and CORS preflights.
2. `RequestIdMiddleware` — reads `X-Request-ID`; keeps it only if it matches `^[A-Za-z0-9._-]{8,64}$`, else `uuid4().hex`. Stores it in a `ContextVar` (read by logging and `error_response`) and `scope["state"]`; echoes it as the `X-Request-ID` response header. Emits one access log line per request: `method`, `path` (no query string), `status`, `latency_ms`, `code` (when error).
3. `StrictCORSMiddleware` — subclass of Starlette `CORSMiddleware` with `allow_origins=settings.allowed_origins`, `allow_methods=["GET","POST"]`, `allow_headers=["Content-Type","X-Request-ID"]`, `expose_headers=["X-Request-ID","Retry-After"]`, `allow_credentials=False`, `max_age=600`. Overrides only the rejected-preflight path: stock Starlette answers a bad preflight with a plain-text 400 that still carries `Access-Control-Allow-Methods/Headers/Max-Age`; ours returns `error_response(cors_rejected)` (403) with **no** `Access-Control-*` headers. Sits inside `RequestIdMiddleware` (so rejected preflights get an ID and an access log line) and outside the error boundary (so error bodies stay readable by the allowed origin).
4. `ErrorBoundaryMiddleware` — catches any exception escaping inner layers; if the response hasn't started, sends `internal_error`; never re-raises (so uvicorn/Starlette never print the exception message). Logs `error_type` and traceback frames (`file:line:function`) **without** the exception message.
5. `BodySizeLimitMiddleware(max_bytes)` — rejects with 413 immediately when `Content-Length` > limit or is non-numeric; otherwise wraps `receive` and counts streamed bytes; on overflow raises `HTTPException(413)` (FastAPI re-raises `HTTPException` from body parsing; any other exception type would be turned into a 400).
6. `TimeoutMiddleware(seconds)` — `asyncio.timeout` around the inner app; on expiry, if the response hasn't started, sends `request_timeout`; logs it either way.

### Logging — `askdata_api/logging_setup.py`
- `configure_logging(level)` installs a `JsonFormatter` on the root logger (stdout), replaces uvicorn's handlers with it and silences `uvicorn.access` (our access line replaces it).
- Each line: `ts` (ISO-8601 UTC), `level`, `logger`, `event` (the log message, always a fixed string literal chosen by our code), `request_id` (from the ContextVar), plus **only** allowlisted extra keys: `method`, `path`, `status`, `latency_ms`, `code`, `provider`, `state`, `error_type`, `frames`. Unknown extras are dropped, so a future `logger.info("x", extra={"question": …})` cannot leak.
- Policy: question text, sample rows and SQL are never logged, in any `APP_ENV`. (Simpler than a production-only rule; revisit if debugging needs it.)

### Tests — `apps/api/tests/`
Shared fixture builds `Settings` directly (no env) and `create_app(settings)`; tests that need slow/raising/body-reading endpoints add routes to that app instance.

- settings: missing `GROQ_API_KEY` → `ValidationError` mentioning the field; error text does **not** contain the present `GEMINI_API_KEY` value; `repr(settings)` contains neither key; `*` / path / trailing slash in origins rejected; `http://` origin rejected in production, `http://localhost:3000` accepted in development; importing `askdata_api.main` with an empty env fails (subprocess).
- errors: unhandled exception → 500 `internal_error`, body has exactly the three keys and no `Traceback`/exception message; `ProviderError("gemini")` with an upstream-looking message → 503 body without it; `RateLimitedError(30)` → 429 + `Retry-After: 30`; 404 and 405 use the envelope; invalid JSON body → 422 `validation_error` without echoing input.
- body limit: `Content-Length` 65 537 → 413 `payload_too_large`; chunked body (no `Content-Length`) over the limit → 413; 65 536 bytes accepted.
- timeout: endpoint sleeping longer than a 0.05 s test timeout → 504 `request_timeout`.
- CORS: preflight from allowed origin → `Access-Control-Allow-Origin` equals that origin; from a disallowed origin → 403 `cors_rejected` with no `Access-Control-*` headers; simple GET from a disallowed origin → no `Access-Control-*` headers; `PUT` preflight from an allowed origin → 403 `cors_rejected`; `Retry-After` listed in `Access-Control-Expose-Headers`.
- headers: the three security headers present on 200, 404 and 500.
- request ID: valid incoming ID echoed in header and error body; malicious value (`"x\nINJECT"`, too long) replaced by a generated one.
- logging: captured JSON lines parse, include `request_id`, and never contain a posted body marker string or a key value; unknown `extra` keys dropped.

## Builder's calls (Claude)
- Rejected CORS preflights use the error envelope (`cors_rejected`, 403) via a `CORSMiddleware` subclass, and request ID moved outside CORS — keeps "every non-2xx uses the envelope" and "no CORS headers for disallowed origins" both true.
- The envelope is guaranteed for responses that pass through our middleware stack. uvicorn's protocol-level 400s (malformed HTTP) and a crash inside `SecurityHeaders`/`RequestId` themselves (Starlette `ServerErrorMiddleware`) are out of scope.
- `Retry-After` is exposed via CORS so the web client can show the wait time on a 429.

## Left to the builder
Exact module/function names beyond those above, test file split, fixture shape, how uvicorn logger handlers are swapped.
