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
