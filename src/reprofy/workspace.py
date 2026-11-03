import shutil
import os
import hashlib
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


def protected_files(root: Path) -> dict[str, bytes]:
    """Snapshot Python and pytest configuration using the same exclusions as copies."""
    result: dict[str, bytes] = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        ignored = _ignore(directory, dirs + files)
        dirs[:] = [name for name in dirs if name not in ignored]
        for name in files:
            path = Path(directory) / name
            if name not in ignored and (path.suffix == ".py" or name in {"pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini", "reprofy.yaml"}):
                result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def project_hashes(root: Path) -> dict[str, str]:
    return {name: hashlib.sha256(content).hexdigest() for name, content in protected_files(root).items()}
