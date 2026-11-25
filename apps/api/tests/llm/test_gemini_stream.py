import httpx
import pytest

from askdata_api.errors import ProviderError
from askdata_api.llm.errors import ProviderOutputError
from tests.llm.fakes import (
    FAKE_KEY,
    MODEL,
    QUESTIONS,
    STREAM_FAILURES,
    Script,
    body_of,
    delta,
    gemini_json,
    provider_for,
    sse,
)

DELTAS = {
    "es": ["Las ventas ", "crecieron un 12 % ", "en 2025."],
    "en": ["Sales ", "grew 12% ", "in 2025."],
}


async def _collect(script: Script, lang: str = "en") -> list[str]:
    provider = provider_for(script)
    return [chunk async for chunk in provider.stream_text(system="s", user=QUESTIONS[lang])]


async def test_stream_request_shape() -> None:
    script = Script([sse(delta("x", finish="STOP"))])

    await _collect(script)

    (request,) = script.requests
    assert request.url.path == f"/v1beta/models/{MODEL}:streamGenerateContent"
    assert request.url.params["alt"] == "sse"
    assert "key" not in request.url.params
    assert request.headers["x-goog-api-key"] == FAKE_KEY
    config = body_of(request)["generationConfig"]
    assert config == {"maxOutputTokens": 2048}


@pytest.mark.parametrize("lang", ["es", "en"])
async def test_stream_yields_deltas_in_order(lang: str) -> None:
    first, second, third = DELTAS[lang]
    script = Script([sse(delta(first), delta(second), delta(third, finish="STOP"))])

    assert await _collect(script, lang) == DELTAS[lang]


async def test_stream_ignores_non_data_lines() -> None:
    script = Script(
        [
            sse(
                delta("Hello "),
                delta("world", finish="STOP"),
                newline="\r\n",
                extra_lines=["", ": keep-alive", "event: message", "id: 7", ""],
            )
        ]
    )

    assert await _collect(script) == ["Hello ", "world"]


async def test_stream_skips_thoughts_and_empty_deltas() -> None:
    script = Script(
        [
            sse(
                gemini_json([], thought="pensando...", finish=None, usage=None),
                delta(""),
                delta("Hola", finish="STOP"),
            )
        ]
    )

    assert await _collect(script, "es") == ["Hola"]


async def test_stream_retries_before_first_delta() -> None:
    script = Script([httpx.Response(503), sse(delta("ok", finish="STOP"))])

    assert await _collect(script) == ["ok"]
    assert len(script.requests) == 2
    assert len(script.sleeps) == 1


async def test_stream_bad_json_after_delta() -> None:
    script = Script(STREAM_FAILURES["bad_json_after_delta"]())
    received: list[str] = []

    with pytest.raises(ProviderOutputError) as exc:
        async for chunk in provider_for(script).stream_text(system="s", user="u"):
            received.append(chunk)

    assert received == ["Hola"]
    assert exc.value.reason == "invalid_json"
    assert len(script.requests) == 1


async def test_stream_transport_error_mid_stream_is_provider_error() -> None:
    script = Script(STREAM_FAILURES["transport_error"]())
    received: list[str] = []

    with pytest.raises(ProviderError) as exc:
        async for chunk in provider_for(script).stream_text(system="s", user="u"):
            received.append(chunk)

    assert type(exc.value) is ProviderError
    assert received == ["Hi"]
    assert len(script.requests) == 1


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (sse(gemini_json(block_reason="SAFETY", usage=None)), "blocked"),
        (sse(delta("Hola"), delta(" mundo", finish="MAX_TOKENS")), "truncated"),
        (sse(delta("Hello"), delta("", finish="SAFETY")), "blocked"),
        (sse(), "empty"),
        (sse(delta("")), "empty"),
        (sse({"unexpected": True}), "empty"),
    ],
)
async def test_stream_output_errors(response: httpx.Response, reason: str) -> None:
    with pytest.raises(ProviderOutputError) as exc:
        await _collect(Script([response]))

    assert exc.value.reason == reason


async def test_stream_4xx_not_retried() -> None:
    script = Script([httpx.Response(400)])

    with pytest.raises(ProviderError):
        await _collect(script)

    assert len(script.requests) == 1
