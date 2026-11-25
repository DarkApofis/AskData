import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from askdata_api.settings import Settings, SettingsError, load_settings
from tests.conftest import GEMINI_TEST_KEY, GROQ_TEST_KEY, make_settings


def test_missing_groq_key_fails() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(  # type: ignore[call-arg]
            gemini_api_key=GEMINI_TEST_KEY,  # type: ignore[arg-type]
            allowed_origins="https://askdata.example",  # type: ignore[arg-type]
        )

    assert "groq_api_key" in str(exc.value)


def test_validation_error_hides_present_secret() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(  # type: ignore[call-arg]
            gemini_api_key=GEMINI_TEST_KEY,  # type: ignore[arg-type]
            allowed_origins="https://askdata.example",  # type: ignore[arg-type]
        )

    assert GEMINI_TEST_KEY not in str(exc.value)


def test_load_settings_error_carries_no_input(monkeypatch: pytest.MonkeyPatch) -> None:
    # pydantic's ValidationError.errors() keeps every input value even with
    # hide_input_in_errors, so startup must surface a sanitized error instead.
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_TEST_KEY)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://askdata.example")

    with pytest.raises(SettingsError) as exc:
        load_settings()

    assert "groq_api_key" in str(exc.value)
    assert GEMINI_TEST_KEY not in repr(vars(exc.value))
    assert GEMINI_TEST_KEY not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__


def test_empty_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(groq_api_key="")


def test_repr_hides_secrets() -> None:
    settings = make_settings()

    for text in (repr(settings), str(settings), str(settings.model_dump())):
        assert GEMINI_TEST_KEY not in text
        assert GROQ_TEST_KEY not in text


def test_secrets_are_readable_explicitly() -> None:
    assert make_settings().gemini_api_key.get_secret_value() == GEMINI_TEST_KEY


@pytest.mark.parametrize("origins", ["*", "https://askdata.example,*"])
def test_origins_reject_wildcard(origins: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(allowed_origins=origins)


@pytest.mark.parametrize(
    "origin",
    [
        "https://askdata.example/",
        "https://askdata.example/app",
        "https://askdata.example?x=1",
        "askdata.example",
        "ftp://askdata.example",
        "https://user@askdata.example",
    ],
)
def test_origins_reject_malformed(origin: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(allowed_origins=origin)


def test_origins_trim_spaces() -> None:
    settings = make_settings(allowed_origins="https://a.example, https://b.example:8443")

    assert settings.allowed_origins == ["https://a.example", "https://b.example:8443"]


@pytest.mark.parametrize("origins", ["https://a.example,,", "", " , "])
def test_origins_reject_empty_entry(origins: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(allowed_origins=origins)


@pytest.mark.parametrize("origin", ["http://askdata.example", "http://localhost:3000"])
def test_production_requires_https(origin: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(app_env="production", allowed_origins=origin)


def test_development_allows_localhost_http() -> None:
    settings = make_settings(allowed_origins="http://localhost:3000,http://127.0.0.1:3000")

    assert settings.allowed_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_development_rejects_non_local_http() -> None:
    with pytest.raises(ValidationError):
        make_settings(allowed_origins="http://askdata.example")


def test_defaults() -> None:
    settings = Settings(
        gemini_api_key=GEMINI_TEST_KEY,  # type: ignore[arg-type]
        groq_api_key=GROQ_TEST_KEY,  # type: ignore[arg-type]
        allowed_origins="https://askdata.example",  # type: ignore[arg-type]
    )

    assert settings.app_env == "production"
    assert settings.max_body_bytes == 65536
    assert settings.request_timeout_seconds == 20
    assert settings.log_level == "INFO"


def test_reads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_TEST_KEY)
    monkeypatch.setenv("GROQ_API_KEY", GROQ_TEST_KEY)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example,https://b.example")

    settings = Settings()  # type: ignore[call-arg]

    assert settings.allowed_origins == ["https://a.example", "https://b.example"]
    assert settings.groq_api_key.get_secret_value() == GROQ_TEST_KEY


def test_import_main_without_env_fails() -> None:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"GEMINI_API_KEY", "GROQ_API_KEY", "ALLOWED_ORIGINS", "APP_ENV"}
    }
    env["GROQ_API_KEY"] = GROQ_TEST_KEY

    result = subprocess.run(
        [sys.executable, "-c", "import askdata_api.main"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode != 0
    assert "gemini_api_key" in result.stderr
    assert GROQ_TEST_KEY not in result.stderr
