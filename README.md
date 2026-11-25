# AskData

Ask questions about a CSV in Spanish or English. The file never leaves the browser.

## Development

Prerequisites: [uv](https://docs.astral.sh/uv/), Node.js 24.

```bash
# From the repo root, once:
uv tool install pre-commit && pre-commit install   # git hooks: gitleaks, ruff, eslint

# API (from apps/api) — copy .env.example to .env and fill it in first
uv sync && uv run pytest
uv run --env-file .env uvicorn askdata_api.main:app --reload   # http://localhost:8000/healthz

# Web (from apps/web)
npm ci && npm test
npm run dev                                        # http://localhost:3000
```

See `docs/` for architecture, threat model and backlog.
