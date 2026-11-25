"""Application settings, read from environment variables only.

Secrets are `SecretStr` and validation errors never echo input values, so a
misconfigured deployment fails at startup without printing a key.
"""

from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
        frozen=True,
    )

    gemini_api_key: SecretStr
    groq_api_key: SecretStr
    allowed_origins: Annotated[list[str], NoDecode]
    app_env: Literal["production", "development"] = "production"
    max_body_bytes: int = Field(default=65_536, ge=1, le=1_048_576)
    request_timeout_seconds: float = Field(default=20, gt=0, le=60)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Interpolated into the provider URL path, hence the strict pattern.
    gemini_model: str = Field(
        default="gemini-3.5-flash-lite", pattern=r"^[a-z0-9][a-z0-9.-]{0,63}$"
    )

    @field_validator("gemini_api_key", "groq_api_key")
    @classmethod
    def _non_empty_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",")]
        return value

    @field_validator("allowed_origins")
    @classmethod
    def _validate_origins(cls, origins: list[str]) -> list[str]:
        if not origins:
            raise ValueError("at least one origin is required")
        for origin in origins:
            _check_origin(origin)
        return origins

    @model_validator(mode="after")
    def _origins_match_environment(self) -> Self:
        for origin in self.allowed_origins:
            parts = urlsplit(origin)
            if parts.scheme == "https":
                continue
            if self.app_env == "production":
                raise ValueError(f"origin {origin!r} must use https in production")
            if parts.hostname not in _LOCAL_HOSTS:
                raise ValueError(f"origin {origin!r}: plain http is only allowed for localhost")
        return self


class SettingsError(RuntimeError):
    """Configuration is missing or invalid. Names fields and error types only."""


def load_settings() -> Settings:
    """Read settings from the environment, failing with a message that cannot
    contain any configured value (pydantic's error objects keep raw inputs)."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'settings'}: {err['type']}"
            for err in exc.errors(include_input=False, include_url=False, include_context=False)
        )
        raise SettingsError(f"Invalid configuration: {problems}") from None


def _check_origin(origin: str) -> None:
    """An origin is exactly scheme://host[:port] — no wildcard, path, query or userinfo."""
    if not origin:
        raise ValueError("empty origin")
    if "*" in origin:
        raise ValueError("wildcard origins are not allowed")
    parts = urlsplit(origin)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"origin {origin!r} must be http(s)://host[:port]")
    if parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise ValueError(f"origin {origin!r} must not have userinfo, path, query or fragment")
    if origin.endswith("?") or origin.endswith("#"):
        raise ValueError(f"origin {origin!r} must not have a query or fragment")
    _ = parts.port  # raises ValueError on an invalid port
