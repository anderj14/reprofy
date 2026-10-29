from dataclasses import dataclass
from typing import Any, Literal, Protocol, Sequence, TypedDict


class Message(TypedDict):
    role: Literal["system", "developer", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class Completion:
    content: str
    reasoning: str | None
    finish_reason: str
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    backend: str
    model: str
    latency_seconds: float


class ModelBackend(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        *,
        reasoning_effort: str,
        max_completion_tokens: int,
        response_format: dict[str, Any] | None = None,
    ) -> Completion:
        ...