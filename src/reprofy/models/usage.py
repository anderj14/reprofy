import json

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .base import Completion, Message, ModelBackend


def usage_by_step(path: Path, bug_id: str) -> dict[str, dict[str, Any]]:
    """Aggregate recorded model usage for one bug, grouped by workflow step."""
    totals: dict[str, dict[str, Any]] = {}
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if len(line) > 65_536:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or record.get("bug_id") != bug_id:
                    continue
                step = str(record.get("step", "unknown"))
                values = totals.setdefault(step, {
                    "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                    "reasoning_tokens": 0, "latency_seconds": 0.0, "models": set(),
                })
                values["calls"] = int(values["calls"]) + 1
                for field in ("prompt_tokens", "completion_tokens", "reasoning_tokens"):
                    values[field] = int(values[field]) + int(record.get(field) or 0)
                values["latency_seconds"] = float(values["latency_seconds"]) + float(record.get("latency_seconds") or 0)
                models = values["models"]
                if isinstance(models, set) and record.get("model"):
                    models.add(str(record["model"]))
    except FileNotFoundError:
        return {}
    for values in totals.values():
        models = values["models"]
        values["models"] = ", ".join(sorted(models)) if isinstance(models, set) else str(models)
    return totals

def log_completion(completion: Completion, path: Path, metadata: Mapping[str, str] | None = None) -> None:
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
    if metadata:
        record.update({key: value for key, value in metadata.items() if key in {"bug_id", "step", "run_id"}})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")

class LoggedBackend:
    def __init__(self, backend: ModelBackend, path: Path, *, metadata: Mapping[str, str] | None = None) -> None:
        self.backend = backend
        self.path = path
        self.metadata = dict(metadata or {})

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
        log_completion(result, self.path, self.metadata)
        return result
