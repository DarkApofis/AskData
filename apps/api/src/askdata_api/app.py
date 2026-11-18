"""Application factory."""

from fastapi import FastAPI

from askdata_api import health
from askdata_api.errors import register_error_handlers
from askdata_api.middleware.body_limit import BodySizeLimitMiddleware
from askdata_api.middleware.cors import StrictCORSMiddleware
from askdata_api.middleware.error_boundary import ErrorBoundaryMiddleware
from askdata_api.middleware.request_id import RequestIdMiddleware
from askdata_api.middleware.security_headers import SecurityHeadersMiddleware
from askdata_api.middleware.timeout import TimeoutMiddleware
from askdata_api.settings import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Without explicit settings they are read from the environment,
    which raises SettingsError on a missing or invalid variable (fail fast)."""
    settings = settings if settings is not None else load_settings()
    app = FastAPI(title="AskData API")
    register_error_handlers(app)
    app.include_router(health.router)

    # add_middleware wraps outward: the last one added is the outermost. Outermost first:
    # SecurityHeaders -> RequestId -> CORS -> ErrorBoundary -> BodyLimit -> Timeout.
    app.add_middleware(TimeoutMiddleware, seconds=settings.request_timeout_seconds)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(ErrorBoundaryMiddleware)
    app.add_middleware(StrictCORSMiddleware, allowed_origins=settings.allowed_origins)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    return app
