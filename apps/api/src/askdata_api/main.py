"""ASGI entry point: `uv run --env-file .env uvicorn askdata_api.main:app`.

Building the app here, at import time, makes a missing or invalid environment
variable abort the server before it accepts connections.
"""

from askdata_api.app import create_app

app = create_app()
