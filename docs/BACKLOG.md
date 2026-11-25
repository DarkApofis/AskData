# Backlog

Items noticed while working on other tasks. Not scheduled.

- Disable FastAPI `/docs`, `/redoc` and `/openapi.json` in production (or gate behind a setting).
- Add Dependabot (or Renovate) to keep pinned Action SHAs and lockfiles current.
- Branch protection on `main` requiring the CI jobs.
- Security headers / CSP for `apps/web` (tracked as A4/A7 in the threat model).
- Freeze pre-commit hook revs to SHAs (`pre-commit autoupdate --freeze`).
- Pin the uv version in CI (`setup-uv` `version:` or `[tool.uv] required-version`) and the exact Node version.
- Widen the eslint pre-commit `files` pattern to `.mts`/`.cts` and to `package.json`/`tsconfig.json`/`eslint.config.mjs`.
- CI concurrency: cancel in-progress runs only for pull requests, not for `main`.
- CI hardening: `npm ci --ignore-scripts`, `NEXT_TELEMETRY_DISABLED=1`, `UV_LOCKED=1`, pytest `--strict-config`.
- Rate limiter (per IP) raising `RateLimitedError`; the error type, 429 mapping and `Retry-After` exposure already exist (B1).
- 422 responses: decide whether to return field locations (never input values) so the client can highlight the field.
- Interpolate `%` args for uvicorn's own startup log lines only (they show templates such as `[%d]` today).
- Timeout after a started response is logged as `unhandled_error` (`ResponseAbortedError`) and the access line shows 200 with no code; background tasks count against the request budget.
- Content-Length check: use `[0-9]{1,20}` instead of `str.isdigit()` (unicode digits / huge values give 500 instead of 413; unreachable through uvicorn's parser today).
- Reject or normalise origins with uppercase letters or default ports (`:443`), which can never match a browser `Origin`.
- Restore `Vary: Origin` on responses to disallowed origins in `StrictCORSMiddleware.send`.
- Replace `# type: ignore[type-arg]` on `_JsonHandler` with `logging.StreamHandler[TextIO]`.
