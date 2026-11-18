"""Application factory."""

from fastapi import FastAPI

from askdata_api import health
from askdata_api.errors import register_error_handlers
from askdata_api.settings import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Without explicit settings they are read from the environment,
    which raises SettingsError on a missing or invalid variable (fail fast)."""
    settings = settings if settings is not None else load_settings()
    app = FastAPI(title="AskData API")
    register_error_handlers(app)
    app.include_router(health.router)
    return app
