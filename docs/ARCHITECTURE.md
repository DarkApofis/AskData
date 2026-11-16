# Architecture

> Stub. Filled in as features land.

## Overview

AskData lets a user upload a CSV in the browser, ask a question in Spanish or
English, and get a validated read-only SQL query, a result table and an
automatically chosen chart.

```
Browser (apps/web, Vercel)                    API (apps/api, Render/Koyeb)
┌──────────────────────────────┐              ┌───────────────────────────┐
│ CSV file (never uploaded)    │              │ FastAPI                   │
│ DuckDB-WASM in a Web Worker  │  schema +    │  - input validation       │
│  - infers schema             │  ≤5 sanitized│  - LLM provider (Gemini / │
│  - runs validated SQL        │  rows +      │    Groq, with fallback)   │
│ Result table + chart         │  question    │  - SQL validation         │
│                              │ ───────────► │    (sqlglot, read-only)   │
│                              │ ◄─────────── │                           │
│                              │  SQL + chart │                           │
└──────────────────────────────┘  hint + code └───────────────────────────┘
```

## Repository layout

| Path | Purpose |
|------|---------|
| `apps/web` | Next.js (App Router) + TypeScript strict + Tailwind. Standalone npm project. |
| `apps/api` | FastAPI on Python 3.12. Standalone uv project. |
| `docs/` | Architecture, threat model, backlog, data sources, specs and plans. |
| `evals/` | LLM evaluation sets (bilingual). Empty for now. |
| `.github/workflows/` | CI. |

## Key decisions

- **Two independent apps, no root JS workspace.** Each deploys from its own
  folder; a workspace would be a premature abstraction with one JS package.
- **uv for Python.** It pins and installs Python 3.12 itself, produces a
  cross-platform lockfile and runs the tools.
- **npm for the web app.** Matches `npm audit` in CI.
- **Privacy by design.** Only schema, at most 5 sanitized sample rows and the
  question leave the browser.
- **Errors carry stable `code` values** with an English fallback message; the
  client localizes by code (es/en).
