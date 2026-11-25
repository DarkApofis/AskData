"""Scripted httpx transport and Gemini response builders. No network."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import SecretStr

from askdata_api.llm.gemini import GeminiProvider

FAKE_KEY = "AIzaSyTEST-not-a-real-key-000"  # gitleaks:allow (fake test value)
MODEL = "gemini-3.5-flash-lite"

QUESTIONS = {
    "es": "¿Cuántas ventas hubo por región en 2025?",
    "en": "How many sales were there per region in 2025?",
}
ANSWERS: dict[str, dict[str, Any]] = {
    "es": {
        "sql": "SELECT region, COUNT(*) AS ventas FROM data GROUP BY region",
        "explanation": "Cuenta las ventas de 2025 agrupadas por región.",
        "response_language": "es",
        "chart_hint": "bar",
        "needs_clarification": False,
        "clarification_question": None,
    },
    "en": {
        "sql": "SELECT region, COUNT(*) AS sales FROM data GROUP BY region",
        "explanation": "Counts 2025 sales grouped by region.",
        "response_language": "en",
        "chart_hint": "bar",
        "needs_clarification": False,
        "clarification_question": None,
    },
}
CLARIFICATIONS: dict[str, dict[str, Any]] = {
    "es": {
        "sql": None,
        "explanation": "No está claro qué columna representa las ventas.",
        "response_language": "es",
        "chart_hint": "table",
        "needs_clarification": True,
        "clarification_question": "¿Las ventas son la columna 'importe' o 'unidades'?",
    },
    "en": {
        "sql": None,
        "explanation": "It is unclear which column holds sales.",
        "response_language": "en",
        "chart_hint": "table",
        "needs_clarification": True,
        "clarification_question": "Are sales the 'amount' or the 'units' column?",
    },
}

Step = httpx.Response | Exception


def gemini_json(
    text_parts: list[str] | None = None,
    *,
    finish: str | None = "STOP",
    block_reason: str | None = None,
    thought: str | None = None,
    no_candidates: bool = False,
    usage: tuple[int, int] | None = (120, 45),
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if usage:
        body["usageMetadata"] = {"promptTokenCount": usage[0], "candidatesTokenCount": usage[1]}
    if block_reason:
        body["promptFeedback"] = {"blockReason": block_reason}
        return body
    if no_candidates:
        return body
    parts: list[dict[str, Any]] = []
    if thought:
        parts.append({"text": thought, "thought": True})
    parts += [{"text": t} for t in text_parts or []]
    candidate: dict[str, Any] = {"content": {"role": "model", "parts": parts}}
    if not parts:
        candidate["content"] = {"role": "model"}
    if finish:
        candidate["finishReason"] = finish
    body["candidates"] = [candidate]
    return body


def ok(answer: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=gemini_json([json.dumps(answer, ensure_ascii=False)]))


def sse(
    *events: dict[str, Any], newline: str = "\n", extra_lines: list[str] | None = None
) -> httpx.Response:
    lines = list(extra_lines or [])
    for event in events:
        lines += [f"data: {json.dumps(event, ensure_ascii=False)}", ""]
    return httpx.Response(
        200,
        content=newline.join(lines).encode(),
        headers={"content-type": "text/event-stream"},
    )


@dataclass
class Script:
    steps: list[Step]
    requests: list[httpx.Request] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def provider_for(script: Script, **kwargs: Any) -> GeminiProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(script.handler))
    return GeminiProvider(
        api_key=SecretStr(FAKE_KEY), model=MODEL, client=client, sleep=script.sleep, **kwargs
    )


def body_of(request: httpx.Request) -> dict[str, Any]:
    data = json.loads(request.content)
    assert isinstance(data, dict)
    return data


ScriptFactory = Callable[[], list[Step]]

# Every failure mode of complete(), reused by the key-leak tests.
COMPLETE_FAILURES: dict[str, ScriptFactory] = {
    "non_json_text": lambda: [httpx.Response(200, json=gemini_json(["not json"]))],
    "html_body": lambda: [httpx.Response(200, content=b"<html>proxy error</html>")],
    "schema_mismatch": lambda: [ok({**ANSWERS["en"], "chart_hint": "pie"})],
    "blocked": lambda: [httpx.Response(200, json=gemini_json(block_reason="SAFETY"))],
    "truncated": lambda: [httpx.Response(200, json=gemini_json(['{"sql"'], finish="MAX_TOKENS"))],
    "empty": lambda: [httpx.Response(200, json=gemini_json(no_candidates=True))],
    "5xx_exhausted": lambda: [httpx.Response(500, json={"error": {"message": FAKE_KEY}})] * 3,
    "429_long": lambda: [httpx.Response(429, headers={"Retry-After": "30"})],
    "401": lambda: [
        httpx.Response(401, json={"error": {"message": f"API key {FAKE_KEY} invalid"}})
    ],
    "timeout": lambda: [httpx.ReadTimeout("timed out")],
    "connect_error": lambda: [httpx.ConnectError("refused")],
}
