# Monorepo Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffold AskData as two independent apps with strict tooling, real tests, secret scanning and a green CI.

**Architecture:** `apps/api` is a standalone uv project; `apps/web` is a standalone npm project; no root JS workspace. One CI workflow with parallel `api`, `web` and `secrets` jobs. pre-commit at the root runs ruff, eslint and gitleaks.

**Tech Stack:** Python 3.12, uv, FastAPI, ruff, mypy, pytest, pytest-asyncio, httpx, pip-audit; Next.js (App Router), TypeScript, Tailwind, ESLint, Prettier, Vitest, Testing Library, jsdom; pre-commit, gitleaks; GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-16-monorepo-scaffold-design.md`

## Global Constraints
- Python `==3.12.*`, pinned via `apps/api/.python-version`.
- mypy `--strict`; TypeScript `"strict": true`.
- `.env*` ignored; `.env.example` tracked and contains only empty values.
- `npm audit --audit-level=high`; `pip-audit` fails on any known vuln.
- Actions pinned to full commit SHAs; workflow `permissions: contents: read`.
- English in code, comments, commits, docs. No dependency without a one-line justification (record in commit body or docs).
- No product features (LLM, SQL, DuckDB, i18n, design).

## Review Focus
1. A test runner collecting zero tests must fail CI, not pass — pytest exits 5 on no tests; Vitest default `passWithNoTests: false` must be kept.
2. `.env.example` must stay tracked while `.env`, `.env.local`, `.env.production` are ignored — verified with `git check-ignore` in Task 1.
3. `POST /healthz` returns 405 and unknown paths 404 (no catch-all) — test in Task 2.
4. gitleaks in CI must scan full history (`fetch-depth: 0`), not just the tip — Task 5.
5. Audits must run against the lockfile actually used (`uv.lock` export, `package-lock.json`), not an unresolved manifest — Task 5.

---

### Task 1: Root hygiene and docs stubs

**Files:** Create `.editorconfig`, `.gitignore`, `apps/api/.env.example`, `apps/web/.env.example`, `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `docs/BACKLOG.md`, `docs/DATA_SOURCES.md`, `evals/README.md`.

- [ ] Write `.gitignore` with `.env*` and `!.env.example`, plus Python/Node/OS artifacts.
- [ ] Verify: `git check-ignore -v .env apps/api/.env.local apps/web/.env.production` prints a match for each; `git check-ignore apps/api/.env.example` exits 1.
- [ ] `.env.example` keys (empty): api `GEMINI_API_KEY=`, `GROQ_API_KEY=`, `ALLOWED_ORIGINS=`; web `NEXT_PUBLIC_API_BASE_URL=`.
- [ ] THREAT_MODEL stub: assets, attackers, abuse-case table with columns `ID | Abuse case | Asset | Likelihood | Impact | Mitigation | Status` covering prompt injection (CSV cells, question), SQL injection/exfiltration, DoS, XSS, secret leakage, quota exhaustion.
- [ ] Commit.

### Task 2: API scaffold

**Files:** Create `apps/api/pyproject.toml`, `apps/api/.python-version`, `apps/api/src/askdata_api/__init__.py`, `apps/api/src/askdata_api/main.py`, `apps/api/src/askdata_api/health.py`, `apps/api/tests/test_health.py`.

**Interfaces:** Produces `askdata_api.main.create_app() -> FastAPI` and ASGI target `askdata_api.main:app`.

- [ ] `uv init`-equivalent pyproject (src layout, hatchling); deps `fastapi`; dev group `ruff`, `mypy`, `pytest`, `pytest-asyncio`, `httpx`, `pip-audit`. Configure ruff (select E,F,I,B,UP,S,ASYNC), mypy strict, pytest `asyncio_mode = "auto"`.
- [ ] Write failing tests: `test_healthz_returns_ok` (GET `/healthz` -> 200, `{"status": "ok"}`), `test_healthz_rejects_post` (-> 405), `test_unknown_path_is_404`.
- [ ] Run `uv run pytest` -> FAIL (import error).
- [ ] Implement `create_app()` including the router from `health.py`. Keep FastAPI's default docs routes; "disable /docs in production" goes to BACKLOG.
- [ ] `uv run pytest` PASS; `uv run ruff check . && uv run ruff format --check . && uv run mypy` clean.
- [ ] Commit (incl. `uv.lock`).

### Task 3: Web scaffold

**Files:** Create `apps/web/package.json`, `tsconfig.json`, `next.config.ts`, `eslint.config.mjs`, `.prettierrc.json`, `postcss.config.mjs`, `vitest.config.mts`, `src/app/layout.tsx`, `src/app/page.tsx`, `src/app/globals.css`, `src/app/page.test.tsx`.

- [ ] Scripts: `dev`, `build`, `lint` (eslint .), `typecheck` (tsc --noEmit), `format:check` (prettier --check .), `test` (vitest run).
- [ ] Failing test `renders the product name`: render `<Home />`, expect heading with name `AskData`.
- [ ] `npm test` -> FAIL.
- [ ] Implement placeholder page (`<main><h1>AskData</h1>…</main>`).
- [ ] `npm test`, `npm run lint`, `npm run typecheck`, `npm run format:check`, `npm run build` all pass.
- [ ] Commit (incl. `package-lock.json`).

### Task 4: pre-commit + gitleaks

**Files:** Create `.pre-commit-config.yaml`.

- [ ] Hooks: `astral-sh/ruff-pre-commit` (ruff-check, ruff-format; files `^apps/api/`), local `eslint` hook (`npm --prefix apps/web run lint`, files `^apps/web/`), `gitleaks/gitleaks` (`gitleaks`). Pin `rev`s.
- [ ] `uv tool install pre-commit && pre-commit install && pre-commit run --all-files` -> pass.
- [ ] Acceptance: stage a file with a fake `ghp_` + 36 random alnum token; `git commit` -> rejected by gitleaks. Remove file.
- [ ] Commit.

### Task 5: CI

**Files:** Create `.github/workflows/ci.yml`.

- [ ] Jobs: `api` (setup-uv, `uv sync --locked`, ruff check, ruff format --check, mypy, pytest, `uv export --frozen --no-hashes --no-emit-project -o req.txt && uv run pip-audit -r req.txt --strict`), `web` (setup-node 24 with npm cache, `npm ci`, lint, format:check, typecheck, test, `npm audit --audit-level=high`), `secrets` (checkout `fetch-depth: 0`, `docker run` pinned gitleaks image `git -v`).
- [ ] SHAs resolved via `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`.
- [ ] Commit, push `chore/scaffold`, `gh run watch` -> all jobs green.
