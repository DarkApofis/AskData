import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from askdata_api.llm.answer import SqlAnswer
from askdata_api.llm.errors import ProviderOutputError, ProviderRateLimited
from tests.conftest import make_settings

VALID_ES: dict[str, Any] = {
    "sql": "SELECT region, COUNT(*) FROM ventas GROUP BY region",
    "explanation": "Cuenta las ventas por región.",
    "response_language": "es",
    "chart_hint": "bar",
    "needs_clarification": False,
    "clarification_question": None,
}
VALID_EN: dict[str, Any] = {
    **VALID_ES,
    "explanation": "Counts sales per region.",
    "response_language": "en",
}
CLARIFY_ES: dict[str, Any] = {
    "sql": None,
    "explanation": "La pregunta es ambigua.",
    "response_language": "es",
    "chart_hint": "table",
    "needs_clarification": True,
    "clarification_question": "¿Te refieres a ventas en unidades o en euros?",
}
CLARIFY_EN: dict[str, Any] = {
    **CLARIFY_ES,
    "explanation": "The question is ambiguous.",
    "response_language": "en",
    "clarification_question": "Do you mean sales in units or in euros?",
}


def test_gemini_model_default() -> None:
    assert make_settings().gemini_model == "gemini-3.5-flash-lite"


@pytest.mark.parametrize("model", ["../x", "a/b", "A", "", "x" * 65, "gemini?key=1"])
def test_gemini_model_rejects(model: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(gemini_model=model)


@pytest.mark.parametrize("data", [VALID_ES, VALID_EN, CLARIFY_ES, CLARIFY_EN])
def test_sql_answer_accepts_valid(data: dict[str, Any]) -> None:
    answer = SqlAnswer.model_validate(data)

    assert answer.response_language == data["response_language"]
    assert answer.needs_clarification == data["needs_clarification"]


@pytest.mark.parametrize(
    "changes",
    [
        {"chart_hint": "pie"},
        {"response_language": "fr"},
        {"extra_field": 1},
        {"needs_clarification": True},  # clarification without a question
        {"needs_clarification": True, "clarification_question": "¿Cuál?"},  # with sql
        {"sql": None},  # answer without sql
        {"sql": "  "},
        {"explanation": ""},
        {"sql": "S" * 4001},
        {"clarification_question": "¿?"},  # question on a non-clarification answer
    ],
)
def test_sql_answer_rejects(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SqlAnswer.model_validate({**VALID_ES, **changes})


def test_schema_has_inline_enums() -> None:
    schema = SqlAnswer.model_json_schema()

    assert "$defs" not in schema
    assert schema["properties"]["chart_hint"]["enum"] == [
        "table",
        "number",
        "bar",
        "line",
        "scatter",
    ]
    assert schema["properties"]["response_language"]["enum"] == ["es", "en"]
    assert set(schema["required"]) == set(VALID_ES)


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    async def output_error() -> None:
        raise ProviderOutputError("gemini", "schema_mismatch")

    async def rate_limited() -> None:
        raise ProviderRateLimited("gemini", retry_after_seconds=7)

    app.add_api_route("/test/output-error", output_error)
    app.add_api_route("/test/rate-limited", rate_limited)
    return app


async def test_output_error_is_503_without_reason(client: httpx.AsyncClient) -> None:
    response = await client.get("/test/output-error")

    assert response.status_code == 503
    assert response.json()["code"] == "provider_unavailable"
    assert "schema_mismatch" not in response.text
    assert "gemini" not in response.text


async def test_provider_rate_limited_is_429_with_retry_after(client: httpx.AsyncClient) -> None:
    response = await client.get("/test/rate-limited")

    assert response.status_code == 429
    assert response.json()["code"] == "rate_limited"
    assert response.headers["Retry-After"] == "7"
    assert "gemini" not in response.text


async def test_handler_logs_provider_and_reason(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        await client.get("/test/output-error")
        await client.get("/test/rate-limited")

    records = [r for r in caplog.records if r.msg == "app_error"]
    assert records[0].__dict__["provider"] == "gemini"
    assert records[0].__dict__["state"] == "schema_mismatch"
    assert records[1].__dict__["provider"] == "gemini"
    assert "state" not in records[1].__dict__
