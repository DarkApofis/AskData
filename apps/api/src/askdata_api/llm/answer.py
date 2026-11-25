"""The structured answer the model must return for a question about a CSV.

`sql` is untrusted model output; it is validated as read-only SQL elsewhere.
Literals (not enums) keep the JSON Schema free of `$defs` for the provider.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ChartHint = Literal["table", "number", "bar", "line", "scatter"]


class SqlAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str | None = Field(max_length=4000)
    explanation: str = Field(min_length=1, max_length=2000)
    response_language: Literal["es", "en"]
    chart_hint: ChartHint
    needs_clarification: bool
    clarification_question: str | None = Field(max_length=500)

    @model_validator(mode="after")
    def _answer_or_clarification(self) -> Self:
        if self.needs_clarification:
            if not (self.clarification_question or "").strip() or self.sql is not None:
                raise ValueError("a clarification needs a question and no sql")
        elif not (self.sql or "").strip() or self.clarification_question is not None:
            raise ValueError("an answer needs sql and no clarification question")
        return self
