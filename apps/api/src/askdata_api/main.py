"""ASGI entry point. Run with: uv run uvicorn askdata_api.main:app"""

from fastapi import FastAPI

from askdata_api import health


def create_app() -> FastAPI:
    app = FastAPI(title="AskData API")
    app.include_router(health.router)
    return app


app = create_app()
