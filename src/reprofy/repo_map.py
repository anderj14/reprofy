from pathlib import Path
from typing import Sequence


IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", ".reprofy"}


def build_repo_map(
    root: Path,
    source_dirs: Sequence[str],
    *,
    max_files: int = 200,
) -> dict[str, str]:
    root = root.resolve()
    modules: dict[str, str] = {}

    for directory in source_dirs:
        source = (root / directory).resolve()
        if not source.is_relative_to(root):
            raise ValueError(f"Source directory escapes project: {directory}")
        if not source.is_dir():
            raise ValueError(f"Source directory does not exist: {directory}")

        for path in source.rglob("*.py"):
            relative = path.relative_to(root)
            if any(part in IGNORED_DIRS for part in relative.parts):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                continue

            module = ".".join(relative.with_suffix("").parts)
            modules[module] = relative.as_posix()
            if len(modules) > max_files:
                raise ValueError(f"Repository exceeds {max_files} Python files")

    return dict(sorted(modules.items()))