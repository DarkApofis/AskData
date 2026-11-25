"""ASGI entry point: `uv run --env-file .env uvicorn askdata_api.main:app`.

Building the app here, at import time, makes a missing or invalid environment
variable abort the server before it accepts connections.
"""

from askdata_api.app import create_app
from askdata_api.logging_setup import configure_logging
from askdata_api.settings import load_settings

settings = load_settings()
configure_logging(settings.log_level)
app = create_app(settings)
