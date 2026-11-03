"""Validate exact edits and render reviewable diffs. No model-written line numbers."""

from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path, PurePosixPath
from typing import Collection

from pydantic import BaseModel, ConfigDict, Field


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    search: str = Field(min_length=1, max_length=100_000)
    replacement: str = Field(max_length=100_000)


class PatchProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explanation: str = Field(min_length=1, max_length=4000)
    edits: list[Edit] = Field(min_length=1, max_length=20)


class PatchError(ValueError):
    pass


def source_path(root: Path, relative: str, allowed_files: Collection[str]) -> Path:
    parsed = PurePosixPath(relative)
    if (
        relative not in allowed_files or parsed.is_absolute()
        or ".." in parsed.parts or "\\" in relative
        or parsed.as_posix() != relative or parsed.suffix != ".py"
    ):
        raise PatchError(f"Edit is not an allowed source path: {relative}")
    path = root
    for part in parsed.parts:
        path = path / part
        if path.is_symlink():
            raise PatchError(f"Symlink edits are forbidden: {relative}")
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise PatchError(f"Source file is missing or outside project: {relative}")
    return path


def read_source(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as stream:
        return stream.read()


def render_diff(path: str, before: str, after: str, *, new_file: bool = False) -> str:
    lines = unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="/dev/null" if new_file else f"a/{path}", tofile=f"b/{path}",
    )
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in lines
    )


@dataclass(frozen=True)
class PreparedPatch:
    originals: dict[str, str]
    updated: dict[str, str]
    diff: str


def prepare_patch(
    root: Path, proposal: PatchProposal, allowed_files: Collection[str],
) -> PreparedPatch:
    originals: dict[str, str] = {}
    updated: dict[str, str] = {}
    for edit in proposal.edits:
        path = source_path(root, edit.path, allowed_files)
        if edit.path not in originals:
            originals[edit.path] = read_source(path)
            updated[edit.path] = originals[edit.path]
        count = updated[edit.path].count(edit.search)
        if count != 1:
            raise PatchError(f"Search in {edit.path} matched {count} times; expected exactly one")
        updated[edit.path] = updated[edit.path].replace(edit.search, edit.replacement, 1)

    if all(updated[path] == before for path, before in originals.items()):
        raise PatchError("Patch does not change source code")
    diff = "".join(render_diff(path, originals[path], updated[path]) for path in sorted(updated))
    return PreparedPatch(originals=originals, updated=updated, diff=diff)


def write_patch_to_workspace(root: Path, prepared: PreparedPatch) -> None:
    """Caller supplies a disposable workspace; this is not a public apply command."""
    for relative, before in prepared.originals.items():
        path = source_path(root, relative, prepared.originals)
        if read_source(path) != before:
            raise PatchError(f"Source changed since patch preparation: {relative}")
    for relative, after in prepared.updated.items():
        path = source_path(root, relative, prepared.originals)
        with path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(after)
