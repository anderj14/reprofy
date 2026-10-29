from collections import deque
from typing import Any, Iterable, Sequence

from .base import Completion, Message

class FakeBackend:
    def __init__(self, responses: Iterable[Completion]) -> None:
        self._responses = deque(responses)
        self.calls: list[dict[str, Any]] = []
    
    def complete(
        self,
        messages: Sequence[Message],
        *,
        reasoning_effort: str,
        max_completion_tokens: int,
        response_format: dict[str, Any] | None = None,
    ) -> Completion:
        self.calls.append({
            "messages": [dict(message) for message in messages],
            "reasoning_effort": reasoning_effort,
            "max_completion_tokens": max_completion_tokens,
            "response_format": response_format,
        })

        if not self._responses:
            raise RuntimeError("FakeBackend has no responses remaining")

        return self._responses.popleft()