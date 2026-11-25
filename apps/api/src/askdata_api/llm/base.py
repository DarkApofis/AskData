"""The provider-agnostic contract every LLM adapter implements."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class Usage:
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class Completion[T: BaseModel]:
    output: T
    usage: Usage
    provider: str
    model: str


class LLMProvider(Protocol):
    name: str

    async def complete[T: BaseModel](
        self, *, system: str, user: str, output_model: type[T]
    ) -> Completion[T]:
        """Return `output_model` validated from the model's JSON output, or raise
        ProviderError / ProviderOutputError / ProviderRateLimited."""
        ...

    def stream_text(self, *, system: str, user: str) -> AsyncIterator[str]:
        """Yield text deltas as they arrive; raise the same typed errors."""
        ...
