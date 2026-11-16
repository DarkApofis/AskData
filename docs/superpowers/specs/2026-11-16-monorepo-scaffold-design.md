# Monorepo scaffold — design (short)

## Intent
Give AskData a secure, testable base before any product code exists: two
independently deployable apps, strict linting/typing, real tests, secret
scanning on commit and in CI, and dependency audits that fail closed.

## Goals
- `apps/api`: FastAPI app factory + `GET /healthz` -> `{"status":"ok"}`; ruff, mypy --strict, pytest, pytest-asyncio.
- `apps/web`: Next.js (App Router) placeholder page; TypeScript strict, ESLint, Prettier, Vitest.
- Root: `.editorconfig`, `.gitignore` (ignores `.env*`, keeps `.env.example`), per-app `.env.example` with empty values, pre-commit (ruff, eslint, gitleaks).
- CI: lint + typecheck + test for both apps, `pip-audit`, `npm audit --audit-level=high`, gitleaks scan.
- Docs stubs: ARCHITECTURE, THREAT_MODEL (abuse-case table), BACKLOG, DATA_SOURCES.

## Non-goals
No LLM code, no SQL validation, no DuckDB, no UI design, no i18n, no deploy config.

## Anti-goals
A key in git; a CI that is green because a step silently did nothing; boilerplate that isn't used.

## Constraints
Free tiers only. Python 3.12 (local machine has 3.14, so the version must be pinned). English everywhere.

## Approaches
- **A. Independent apps, one CI workflow with a job per app** — chosen. Simplest; each app deploys from its own folder (Vercel / Render).
- B. Root npm workspace wrapping the web app — rejected: premature abstraction with one JS package.
- C. Path-filtered workflow per app — rejected: required checks can hang on unchanged paths; overkill.

## Decided by the user
- Approach A.
- Push branch `chore/scaffold` to `origin` to prove CI green.

## Builder's calls (Claude)
- **uv over pip-tools**: uv pins and downloads Python 3.12 itself, produces a cross-platform lockfile, and is one tool for venv + deps + running tools.
- **npm** (not pnpm) to match the requested `npm audit`.
- Health test uses `httpx.AsyncClient` + `ASGITransport` under pytest-asyncio (httpx is already in the stack).
- Web test renders the placeholder page with Testing Library + jsdom (the only way to make the Vitest test real at this stage).
- Tailwind is included because it is a stack constraint; only the base import, no design.
- `pip-audit` has no severity filter, so it fails on any known vuln — stricter than "high", which fails closed.
- GitHub Actions pinned to commit SHAs; workflow `permissions: contents: read`.
- gitleaks runs in CI via its pinned Docker image (no license needed, no third-party action).
- Acceptance for the hook: a commit containing a fake GitHub-PAT-shaped token is rejected locally.
