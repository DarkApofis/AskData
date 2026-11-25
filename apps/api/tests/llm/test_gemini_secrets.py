"""The API key must never reach logs, exceptions or reprs — even with DEBUG logging
on every logger, including httpx and httpcore."""

import logging

import pytest
from pydantic import SecretStr

from askdata_api.errors import AppError
from askdata_api.llm.answer import SqlAnswer
from tests.llm.fakes import (
    COMPLETE_FAILURES,
    FAKE_KEY,
    STREAM_FAILURES,
    Script,
    ScriptFactory,
    provider_for,
)


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore", "askdata_api"):
        caplog.set_level(logging.DEBUG, logger=name)
    return caplog


@pytest.mark.parametrize("make_steps", COMPLETE_FAILURES.values(), ids=COMPLETE_FAILURES.keys())
async def test_key_never_leaks_from_complete(
    make_steps: ScriptFactory, debug_logs: pytest.LogCaptureFixture
) -> None:
    provider = provider_for(Script(make_steps()))

    with pytest.raises(AppError) as exc:
        await provider.complete(system="s", user="u", output_model=SqlAnswer)

    assert exc.value.__cause__ is None
    for link in _exception_chain(exc.value):
        assert FAKE_KEY not in str(link)
        assert FAKE_KEY not in repr(link)
        assert FAKE_KEY not in repr(vars(link))
    assert FAKE_KEY not in debug_logs.text
    assert FAKE_KEY not in str([r.__dict__ for r in debug_logs.records])


def test_repr_hides_key() -> None:
    provider = provider_for(Script([]))

    assert FAKE_KEY not in repr(provider)
    assert FAKE_KEY not in str(vars(provider))
    assert isinstance(vars(provider)["_api_key"], SecretStr)


@pytest.mark.parametrize("make_steps", STREAM_FAILURES.values(), ids=STREAM_FAILURES.keys())
async def test_key_never_leaks_from_stream(
    make_steps: ScriptFactory, debug_logs: pytest.LogCaptureFixture
) -> None:
    provider = provider_for(Script(make_steps()))

    with pytest.raises(AppError) as exc:
        async for _ in provider.stream_text(system="s", user="u"):
            pass

    assert exc.value.__cause__ is None
    for link in _exception_chain(exc.value):
        assert FAKE_KEY not in str(link)
        assert FAKE_KEY not in repr(link)
        assert FAKE_KEY not in repr(vars(link))
    assert FAKE_KEY not in debug_logs.text
    assert FAKE_KEY not in str([r.__dict__ for r in debug_logs.records])
