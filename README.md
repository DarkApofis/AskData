# AskData

Ask questions about a CSV in Spanish or English. The file never leaves the browser.

## Development

Prerequisites: [uv](https://docs.astral.sh/uv/), Node.js 24.

```bash
uv tool install pre-commit && pre-commit install   # git hooks: gitleaks, ruff, eslint

cd apps/api && uv sync && uv run pytest            # API
uv run uvicorn askdata_api.main:app --reload       # http://localhost:8000/healthz

cd apps/web && npm ci && npm test                  # web
npm run dev                                        # http://localhost:3000
```

See `docs/` for architecture, threat model and backlog.
