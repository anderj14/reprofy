"""Review, reverify and apply structured proposals with explicit human approval."""

import os
import shutil
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .patches import render_diff
from .test_runner import run_pytest, summarize_test_run
from .workspace import project_hashes, protected_files, temporary_workspace
from .store import StoreError, YamlBugStore

if TYPE_CHECKING:
    from .fixing import FixResult


class ApplyError(ValueError):
    pass


class ArtifactChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    before: str | None
    after: str


class ApplyProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    status: Literal["tests_verified"] = "tests_verified"
    project_root: str
    report: str = Field(min_length=1)
    expected_behavior: str = Field(min_length=1)
    explanation: str
    changes: list[ArtifactChange] = Field(min_length=2, max_length=21)
    baseline_hashes: dict[str, str] = Field(min_length=1)
    source_files: list[str] = Field(min_length=1)
    reproduction_path: str = "tests/repro/test_reprofy_generated.py"
    suite_targets: list[str] = Field(default_factory=lambda: ["tests"], min_length=1)
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    diff: str = Field(min_length=1)
    test_command: list[str] = Field(default_factory=lambda: ["{python}", "-m", "pytest"])
    bug_id: str | None = None
    bug_revision: int | None = Field(default=None, ge=0)

    @field_validator("test_command")
    @classmethod
    def supported_command(cls, value: list[str]) -> list[str]:
        if value[:3] != ["{python}", "-m", "pytest"]:
            raise ValueError("Only Python/pytest commands are supported")
        return value


def _target(root: Path, relative: str) -> Path:
    root = root.resolve()
    parsed = PurePosixPath(relative)
    if (
        not relative or parsed.is_absolute() or ".." in parsed.parts
        or "\\" in relative or parsed.as_posix() != relative
        or any(part in {".git", ".reprofy"} for part in parsed.parts)
    ):
        raise ApplyError(f"Unsafe project path: {relative}")
    path = root
    for part in parsed.parts:
        path = path / part
        if path.is_symlink():
            raise ApplyError(f"Symlink paths are forbidden: {relative}")
    if not path.resolve().is_relative_to(root):
        raise ApplyError(f"Path escapes project: {relative}")
    return path


def prepare_application(project: Path, proposal: ApplyProposal) -> str:
    root = project.resolve()
    if not root.is_dir() or str(root) != proposal.project_root:
        raise ApplyError("Proposal belongs to a different project directory")
    if project_hashes(root) != proposal.baseline_hashes:
        raise ApplyError("Project source, tests or configuration changed; generate a new proposal")
    if (proposal.bug_id is None) != (proposal.bug_revision is None):
        raise ApplyError("Bug ID and revision must be supplied together")
    if proposal.bug_id:
        bug = YamlBugStore(root).get(proposal.bug_id)
        if bug.status != "fix_verified" or bug.revision != proposal.bug_revision:
            raise ApplyError("Bug status/revision changed; inspect the latest bug record")
        if bug.report.text != proposal.report or bug.report.expected != proposal.expected_behavior:
            raise ApplyError("Proposal does not match the bug report and expected behavior")
    repro = PurePosixPath(proposal.reproduction_path)
    if repro.parts[:2] != ("tests", "repro") or repro.suffix != ".py" or not repro.name.startswith("test_"):
        raise ApplyError("Regression tests must be new test_*.py files under tests/repro/")
    seen: set[str] = set()
    additions = sources = 0
    diffs: dict[str, str] = {}
    for change in proposal.changes:
        if change.path in seen:
            raise ApplyError(f"Duplicate changed path: {change.path}")
        seen.add(change.path)
        target = _target(root, change.path)
        if target.suffix != ".py" or change.before == change.after:
            raise ApplyError(f"Expected a changed Python file: {change.path}")
        if change.before is None:
            if change.path != proposal.reproduction_path or target.exists():
                raise ApplyError("Regression test must be new and must not overwrite an existing file")
            additions += 1
        else:
            if change.path not in proposal.source_files or "tests" in PurePosixPath(change.path).parts or target.name == "conftest.py":
                raise ApplyError(f"Existing file is not editable source: {change.path}")
            if not target.is_file() or target.read_bytes() != change.before.encode("utf-8"):
                raise ApplyError(f"Source changed: {change.path}")
            sources += 1
        diffs[change.path] = render_diff(
            change.path, change.before or "", change.after, new_file=change.before is None,
        )
    if additions != 1 or sources < 1:
        raise ApplyError("Proposal must include source changes and exactly one new regression test")
    for target in proposal.suite_targets:
        path = _target(root, target)
        if target.startswith("-") or not path.exists():
            raise ApplyError(f"Invalid pytest target: {target}")
    source_names = sorted(name for name in diffs if name != proposal.reproduction_path)
    diff = "".join(diffs[name] for name in source_names) + diffs[proposal.reproduction_path]
    if diff != proposal.diff:
        raise ApplyError("Saved diff does not match the structured changes")
    return diff


def create_apply_proposal(
    project: Path, result: "FixResult", *, report: str, expected_behavior: str,
) -> ApplyProposal:
    if result.status != "tests_verified" or not result.changes or not result.baseline_hashes:
        raise ApplyError("Only structured, test-verified results can become apply proposals")
    proposal = ApplyProposal(
        project_root=str(project.resolve()), report=report, expected_behavior=expected_behavior,
        explanation=result.explanation,
        changes=[ArtifactChange(**asdict(change)) for change in result.changes],
        baseline_hashes=result.baseline_hashes, source_files=list(result.source_files),
        suite_targets=list(result.suite_targets), diff=result.diff,
        reproduction_path=result.reproduction_path,
        test_command=list(result.test_command), timeout_seconds=result.timeout_seconds,
    )
    prepare_application(project, proposal)
    return proposal


def load_apply_proposal(path: Path) -> ApplyProposal:
    with path.open("rb") as stream:
        content = stream.read(5_000_001)
    if len(content) > 5_000_000:
        raise ApplyError("Proposal exceeds the 5 MB limit")
    return ApplyProposal.model_validate_json(content, strict=True)


def verify_application(project: Path, proposal: ApplyProposal) -> None:
    prepare_application(project, proposal)
    with temporary_workspace(project) as workspace:
        baseline = protected_files(workspace)
        run = run_pytest(workspace, proposal.suite_targets, timeout_seconds=proposal.timeout_seconds, test_command=proposal.test_command, project_root=project)
        if summarize_test_run(run).status != "passed" or protected_files(workspace) != baseline:
            raise ApplyError(f"Baseline no longer passes unchanged:\n{run.output}")
        for change in proposal.changes:
            target = _target(workspace, change.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(change.after.encode("utf-8"))
        expected = protected_files(workspace)
        for targets in [[proposal.reproduction_path], proposal.suite_targets]:
            run = run_pytest(workspace, targets, timeout_seconds=proposal.timeout_seconds, test_command=proposal.test_command, project_root=project)
            if summarize_test_run(run).status != "passed" or protected_files(workspace) != expected:
                raise ApplyError(f"Saved patch no longer verifies unchanged:\n{run.output}")
    prepare_application(project, proposal)


@dataclass(frozen=True)
class ApplyResult:
    status: str
    changed_files: tuple[str, ...] = ()
    bookkeeping_warning: str | None = None


def _persist_outcome(project: Path, proposal: ApplyProposal, status: str) -> str | None:
    if proposal.bug_id is None:
        return None
    try:
        store = YamlBugStore(project)
        bug = store.get(proposal.bug_id)
        if bug.revision != proposal.bug_revision or bug.status != "fix_verified":
            raise StoreError("Bug record changed before outcome could be recorded")
        updates = {"status": status}
        if status == "applied" and bug.repro is not None:
            updates["repro"] = bug.repro.model_copy(update={"fails_as_expected": True})
        store.save(bug.model_copy(update=updates))
    except (StoreError, OSError) as error:
        return f"Filesystem outcome is {status}, but bug status could not be saved: {error}"
    return None


def _replace(staged: Path, target: Path) -> None:
    os.replace(staged, target)


def _write_transaction(root: Path, proposal: ApplyProposal) -> None:
    metadata = root / ".reprofy"
    if metadata.is_symlink():
        raise ApplyError("Project .reprofy must not be a symlink")
    metadata.mkdir(exist_ok=True)
    lock = metadata / "apply.lock"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ApplyError("An apply lock exists; inspect any unfinished apply before retrying") from error
    os.close(descriptor)
    staging: Path | None = None
    written: list[ArtifactChange] = []
    created_dirs: list[Path] = []
    backups: dict[str, Path] = {}
    keep_recovery = False
    try:
        prepare_application(root, proposal)
        staging = Path(tempfile.mkdtemp(prefix="apply-", dir=metadata))
        (staging / "recovery.json").write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
        staged: dict[str, Path] = {}
        for index, change in enumerate(proposal.changes):
            target = _target(root, change.path)
            mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
            candidate = staging / f"{index}.new"
            candidate.write_bytes(change.after.encode("utf-8"))
            candidate.chmod(mode)
            staged[change.path] = candidate
            if change.before is not None:
                backup = staging / f"{index}.backup"
                backup.write_bytes(change.before.encode("utf-8"))
                backup.chmod(mode)
                backups[change.path] = backup
        prepare_application(root, proposal)
        for change in proposal.changes:
            target = _target(root, change.path)
            if change.before is None:
                if target.exists():
                    raise ApplyError("Regression file appeared during apply")
            elif target.read_bytes() != change.before.encode("utf-8"):
                raise ApplyError(f"Source changed during apply: {change.path}")
            missing: list[Path] = []
            parent = target.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                created_dirs.append(directory)
            target = _target(root, change.path)
            _replace(staged[change.path], target)
            written.append(change)
    except BaseException as error:
        problems: list[str] = []
        for change in reversed(written):
            try:
                target = _target(root, change.path)
                if not target.is_file() or target.read_bytes() != change.after.encode("utf-8"):
                    raise ApplyError("File changed concurrently; refusing to overwrite it during recovery")
                if change.before is None:
                    target.unlink()
                else:
                    _replace(backups[change.path], target)
            except BaseException as recovery_error:
                problems.append(f"{change.path}: {recovery_error}")
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        if problems:
            keep_recovery = True
            raise ApplyError(f"Apply interrupted; recovery files kept at {staging}: {problems}") from error
        raise ApplyError(f"Apply aborted; written source/test changes restored: {error}") from error
    finally:
        if staging is not None and not keep_recovery:
            shutil.rmtree(staging)
        lock.unlink(missing_ok=True)


def apply_proposal(
    project: Path, proposal: ApplyProposal, *, approve: Callable[[str], bool],
) -> ApplyResult:
    # Work with a private copy so a callback cannot replace reviewed changes in memory.
    proposal = ApplyProposal.model_validate_json(proposal.model_dump_json(), strict=True)
    diff = prepare_application(project, proposal)
    verify_application(project, proposal)
    if approve(diff) is not True:
        return ApplyResult("rejected", bookkeeping_warning=_persist_outcome(project, proposal, "rejected"))
    prepare_application(project, proposal)
    _write_transaction(project.resolve(), proposal)
    return ApplyResult("applied", tuple(change.path for change in proposal.changes), _persist_outcome(project, proposal, "applied"))
