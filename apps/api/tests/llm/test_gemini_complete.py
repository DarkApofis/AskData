import json

import httpx
import pytest

from askdata_api.errors import ProviderError
from askdata_api.llm.answer import SqlAnswer
from askdata_api.llm.errors import ProviderOutputError, ProviderRateLimited
from tests.llm.fakes import (
    ANSWERS,
    CLARIFICATIONS,
    FAKE_KEY,
    MODEL,
    QUESTIONS,
    Script,
    body_of,
    gemini_json,
    ok,
    provider_for,
)

SYSTEM = "You translate questions about a CSV into SQL."


async def _complete(script: Script, lang: str = "en") -> SqlAnswer:
    provider = provider_for(script)
    result = await provider.complete(system=SYSTEM, user=QUESTIONS[lang], output_model=SqlAnswer)
    return result.output


async def test_request_shape() -> None:
    script = Script([ok(ANSWERS["es"])])

    await _complete(script, "es")

    (request,) = script.requests
    assert request.method == "POST"
    assert request.url.path == f"/v1beta/models/{MODEL}:generateContent"
    assert request.url.host == "generativelanguage.googleapis.com"
    assert request.headers["x-goog-api-key"] == FAKE_KEY
    assert "key" not in request.url.params
    assert FAKE_KEY not in str(request.url)
    body = body_of(request)
    assert body["systemInstruction"] == {"parts": [{"text": SYSTEM}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": QUESTIONS["es"]}]}]
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == SqlAnswer.model_json_schema()
    assert config["maxOutputTokens"] == 2048
    for forbidden in ("temperature", "topP", "topK", "thinkingConfig"):
        assert forbidden not in config


@pytest.mark.parametrize("lang", ["es", "en"])
async def test_complete_success(lang: str) -> None:
    provider = provider_for(Script([ok(ANSWERS[lang])]))

    result = await provider.complete(system=SYSTEM, user=QUESTIONS[lang], output_model=SqlAnswer)

    assert result.output == SqlAnswer.model_validate(ANSWERS[lang])
    assert result.output.response_language == lang
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 45
    assert result.usage.latency_ms >= 0
    assert result.provider == "gemini"
    assert result.model == MODEL


async def test_missing_usage_is_none() -> None:
    script = Script(
        [httpx.Response(200, json=gemini_json([json.dumps(ANSWERS["en"])], usage=None))]
    )
    provider = provider_for(script)

    result = await provider.complete(system=SYSTEM, user="q", output_model=SqlAnswer)

    assert result.usage.input_tokens is None
    assert result.usage.output_tokens is None


@pytest.mark.parametrize("lang", ["es", "en"])
async def test_complete_clarification(lang: str) -> None:
    answer = await _complete(Script([ok(CLARIFICATIONS[lang])]), lang)

    assert answer.needs_clarification
    assert answer.sql is None
    assert answer.response_language == lang


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(200, json=gemini_json(["not json at all"])), "invalid_json"),
        (httpx.Response(200, content=b"<html>proxy error</html>"), "invalid_json"),
        (httpx.Response(200, json=["unexpected", "list"]), "empty"),
        (ok({**ANSWERS["en"], "chart_hint": "pie"}), "schema_mismatch"),
        (ok({**ANSWERS["en"], "extra": 1}), "schema_mismatch"),
        (httpx.Response(200, json=gemini_json(["[1, 2]"])), "schema_mismatch"),
        (httpx.Response(200, json=gemini_json(block_reason="SAFETY")), "blocked"),
        (httpx.Response(200, json=gemini_json(['{"sql": "SE'], finish="MAX_TOKENS")), "truncated"),
        (
            httpx.Response(200, json=gemini_json([json.dumps(ANSWERS["en"])], finish="SAFETY")),
            "blocked",
        ),
        (httpx.Response(200, json=gemini_json(no_candidates=True)), "empty"),
        (httpx.Response(200, json=gemini_json([])), "empty"),
    ],
)
async def test_output_errors(response: httpx.Response, reason: str) -> None:
    with pytest.raises(ProviderOutputError) as exc:
        await _complete(Script([response]))

    assert exc.value.reason == reason
    assert exc.value.provider == "gemini"


async def test_parts_are_joined() -> None:
    text = json.dumps(ANSWERS["en"])
    script = Script([httpx.Response(200, json=gemini_json([text[:20], text[20:]]))])

    assert (await _complete(script)).sql == ANSWERS["en"]["sql"]


async def test_thought_parts_are_skipped() -> None:
    body = gemini_json([json.dumps(ANSWERS["es"])], thought="Primero pienso en la consulta...")

    assert (await _complete(Script([httpx.Response(200, json=body)]), "es")).chart_hint == "bar"


async def test_retries_5xx_then_succeeds() -> None:
    script = Script([httpx.Response(503), httpx.Response(503), ok(ANSWERS["en"])])

    await _complete(script)

    assert len(script.requests) == 3
    assert len(script.sleeps) == 2


async def test_5xx_exhausted_is_provider_error() -> None:
    script = Script([httpx.Response(500)] * 3)

    with pytest.raises(ProviderError) as exc:
        await _complete(script)

    assert type(exc.value) is ProviderError
    assert len(script.requests) == 3
    assert len(script.sleeps) == 2


async def test_429_waits_retry_after_then_succeeds() -> None:
    script = Script([httpx.Response(429, headers={"Retry-After": "2"}), ok(ANSWERS["en"])])

    await _complete(script)

    assert len(script.requests) == 2
    assert script.sleeps[0] >= 2


async def test_429_long_retry_after_fails_fast() -> None:
    body = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s"}],
        }
    }
    script = Script([httpx.Response(429, json=body)])

    with pytest.raises(ProviderRateLimited) as exc:
        await _complete(script)

    assert exc.value.retry_after_seconds == 30
    assert exc.value.provider == "gemini"
    assert len(script.requests) == 1
    assert script.sleeps == []


async def test_429_exhausted() -> None:
    script = Script([httpx.Response(429, headers={"Retry-After": "1"})] * 3)

    with pytest.raises(ProviderRateLimited) as exc:
        await _complete(script)

    assert exc.value.retry_after_seconds == 1
    assert len(script.requests) == 3


async def test_429_without_retry_after() -> None:
    script = Script([httpx.Response(429)] * 3)

    with pytest.raises(ProviderRateLimited) as exc:
        await _complete(script)

    assert exc.value.retry_after_seconds is None


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_4xx_not_retried(status: int) -> None:
    script = Script([httpx.Response(status)])

    with pytest.raises(ProviderError) as exc:
        await _complete(script)

    assert type(exc.value) is ProviderError
    assert len(script.requests) == 1


@pytest.mark.parametrize("error", [httpx.ReadTimeout("t"), httpx.ConnectError("refused")])
async def test_transport_errors_not_retried(error: Exception) -> None:
    script = Script([error])

    with pytest.raises(ProviderError):
        await _complete(script)

    assert len(script.requests) == 1
    assert script.sleeps == []


async def test_per_attempt_timeout_is_sent() -> None:
    script = Script([ok(ANSWERS["en"])])

    await provider_for(script, timeout_seconds=3.0).complete(
        system=SYSTEM, user="q", output_model=SqlAnswer
    )

    assert script.requests[0].extensions["timeout"] == {
        "connect": 3.0,
        "read": 3.0,
        "write": 3.0,
        "pool": 3.0,
    }


async def test_deeply_nested_json_is_invalid_json() -> None:
    with pytest.raises(ProviderOutputError) as exc:
        await _complete(Script([httpx.Response(200, content=b"[" * 100_000)]))

    assert exc.value.reason == "invalid_json"
