from pathlib import Path
from typing import Sequence


def gather_context(
    root: Path,
    paths: Sequence[str],
    *,
    max_chars: int = 40_000,
) -> dict[str, str]:
    if max_chars < 1:
        raise ValueError("Context budget must be positive")

    root = root.resolve()
    context: dict[str, str] = {}
    remaining = max_chars

    for relative in paths:
        path = (root / relative).resolve()

        if not path.is_relative_to(root):
            raise ValueError(f"Context path escapes project: {relative}")
        if path.suffix != ".py" or not path.is_file():
            raise ValueError(f"Expected a Python file: {relative}")

        key = path.relative_to(root).as_posix()
        if key in context:
            continue

        with path.open(encoding="utf-8") as file:
            content = file.read(remaining + 1)

        if len(content) > remaining:
            raise ValueError(f"Context exceeds {max_chars} characters")

        context[key] = content
        remaining -= len(content)

    return context