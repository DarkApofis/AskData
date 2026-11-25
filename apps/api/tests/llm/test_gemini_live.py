"""Real Gemini calls. Deselected by default; run with:

    GEMINI_API_KEY=... uv run pytest -m live

Settles what the docs do not confirm: the x-goog-api-key header on generateContent,
whether responseJsonSchema accepts SqlAnswer's schema, the SSE event shape and that
streams end with a finishReason. It does not trigger a 429, so where Gemini puts the
retry delay stays unverified (parse_retry_after reads both the header and RetryInfo).
"""

import os

import httpx
import pytest
from pydantic import SecretStr

from askdata_api.llm.answer import SqlAnswer
from askdata_api.llm.gemini import GeminiProvider
from tests.llm.fakes import QUESTIONS

# Read at import: the autouse env-isolation fixture removes it before each test.
_LIVE_KEY = os.environ.get("GEMINI_API_KEY")
_LIVE_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not _LIVE_KEY, reason="GEMINI_API_KEY not set"),
]

SYSTEM = (
    "You translate a question about a single DuckDB table named data into one read-only "
    "SELECT statement. Columns: region (VARCHAR), amount (DOUBLE), sold_at (DATE). "
    "Write `explanation` and any `clarification_question` in the language of the question, "
    "and set `response_language` to that language."
)


def _provider(client: httpx.AsyncClient) -> GeminiProvider:
    assert _LIVE_KEY is not None
    return GeminiProvider(
        api_key=SecretStr(_LIVE_KEY), model=_LIVE_MODEL, client=client, timeout_seconds=20.0
    )


@pytest.mark.parametrize("lang", ["es", "en"])
async def test_live_complete(lang: str) -> None:
    async with httpx.AsyncClient() as client:
        result = await _provider(client).complete(
            system=SYSTEM, user=QUESTIONS[lang], output_model=SqlAnswer
        )

    assert result.output.response_language == lang
    assert result.usage.input_tokens


async def test_live_stream() -> None:
    async with httpx.AsyncClient() as client:
        chunks = [
            chunk
            async for chunk in _provider(client).stream_text(
                system="Answer in one short sentence.", user=QUESTIONS["es"]
            )
        ]

    assert chunks
