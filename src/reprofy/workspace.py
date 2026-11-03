import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator


EXCLUDED = shutil.ignore_patterns(
    ".git",
    ".venv",
    "venv",
    ".reprofy",
    ".env",
    ".env.*",
    "__pycache__",
    ".pytest_cache",
    "*.egg-info",
)


def _ignore(directory: str, names: list[str]) -> set[str]:
    excluded = set(EXCLUDED(directory, names))
    excluded.update(
        name for name in names
        if (Path(directory) / name).is_symlink()
    )
    return excluded

@contextmanager
def temporary_workspace(root: Path) -> Iterator[Path]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Project directory does not exist: {root}")

    with TemporaryDirectory(prefix="reprofy-") as directory:
        destination = Path(directory) / "project"
        shutil.copytree(root, destination, ignore=_ignore)
        yield destination