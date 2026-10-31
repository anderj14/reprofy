import json

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .base import Completion, Message, ModelBackend

def log_completion(completion: Completion, path: Path) -> None:
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "backend": completion.backend,
        "model": completion.model,
        "finish_reason": completion.finish_reason,
        "prompt_tokens": completion.prompt_tokens,
        "completion_tokens": completion.completion_tokens,
        "reasoning_tokens": completion.reasoning_tokens,
        "latency_seconds": completion.latency_seconds,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")

class LoggedBackend:
    def __init__(self, backend: ModelBackend, path: Path) -> None:
        self.backend = backend
        self.path = path

    def complete(
        self,
        messages: Sequence[Message],
        *,
        reasoning_effort: str,
        max_completion_tokens: int,
        response_format: dict[str, Any] | None = None,
    ) -> Completion:
        result = self.backend.complete(
            messages,
            reasoning_effort=reasoning_effort,
            max_completion_tokens=max_completion_tokens,
            response_format=response_format,
        )
        log_completion(result, self.path)
        return result