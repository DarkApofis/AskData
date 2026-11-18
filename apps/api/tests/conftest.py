from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from askdata_api.app import create_app
from askdata_api.settings import Settings

GEMINI_TEST_KEY = "gem-test-key-123"  # gitleaks:allow (fake test value)
GROQ_TEST_KEY = "groq-test-key-456"  # gitleaks:allow (fake test value)

_DEFAULTS: dict[str, object] = {
    "app_env": "development",
    "gemini_api_key": GEMINI_TEST_KEY,
    "groq_api_key": GROQ_TEST_KEY,
    "allowed_origins": "http://localhost:3000,https://askdata.example",
}


def make_settings(**overrides: object) -> Settings:
    """Build Settings from explicit values so tests never depend on the real env."""
    return Settings(**{**_DEFAULTS, **overrides})  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "ALLOWED_ORIGINS",
        "APP_ENV",
        "MAX_BODY_BYTES",
        "REQUEST_TIMEOUT_SECONDS",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # raise_app_exceptions=False: assert on what a real client receives.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
