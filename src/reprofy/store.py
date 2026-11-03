"""One YAML file per bug, with atomic writes and optimistic revisions."""

import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Protocol

import yaml
from pydantic import ValidationError

from .bugs import Bug, BugReport, TRANSITIONS
from .config import ConfigError, project_path
from .yaml_io import YamlDataError, parse_yaml_mapping


class StoreError(ValueError):
    pass


class BugNotFound(StoreError):
    pass


class BugStore(Protocol):
    def create(self, report: BugReport) -> Bug: ...
    def get(self, bug_id: str) -> Bug: ...
    def save(self, bug: Bug) -> Bug: ...
    def list(self) -> list[Bug]: ...


def dump_bug(bug: Bug) -> str:
    return yaml.safe_dump(bug.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True)


class YamlBugStore:
    def __init__(self, project: Path):
        self.project = project.resolve()
        if not self._directory().is_dir():
            raise StoreError("Bug storage is missing; run reprofy init first")

    def _directory(self) -> Path:
        try:
            return project_path(self.project, ".reprofy/bugs")
        except ConfigError as error:
            raise StoreError(str(error)) from error

    def _path(self, bug_id: str) -> Path:
        if len(bug_id) > 64 or not re.fullmatch(r"BUG-[0-9]{3,}", bug_id):
            raise StoreError("Invalid bug ID; expected BUG-001")
        number = int(bug_id[4:])
        if number < 1 or bug_id != f"BUG-{number:03d}":
            raise StoreError("Invalid bug ID; expected canonical BUG-001 numbering")
        try:
            return project_path(self.project, f".reprofy/bugs/{bug_id}.yaml")
        except ConfigError as error:
            raise StoreError(str(error)) from error

    @contextmanager
    def _lock(self) -> Iterator[None]:
        lock = self._directory() / ".store.lock"
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise StoreError("Bug store is locked; inspect any active/interrupted writer before retrying") from error
        try:
            os.close(descriptor)
            yield
        finally:
            lock.unlink(missing_ok=True)

    def get(self, bug_id: str) -> Bug:
        path = self._path(bug_id)
        if not path.is_file():
            raise BugNotFound(f"Bug not found: {bug_id}")
        try:
            with path.open(encoding="utf-8") as stream:
                text = stream.read(262_145)
            if len(text) > 262_144:
                raise StoreError(f"Bug record exceeds the size limit: {bug_id}")
            bug = Bug.model_validate(parse_yaml_mapping(text))
        except (ValidationError, YamlDataError, UnicodeError) as error:
            raise StoreError(f"Invalid bug record: {bug_id}; no changes were made") from error
        if bug.id != bug_id:
            raise StoreError(f"Bug record ID does not match its filename: {bug_id}")
        return bug

    def list(self) -> list[Bug]:
        bugs = [self.get(path.stem) for path in self._directory().glob("*.yaml")]
        return sorted(bugs, key=lambda bug: int(bug.id[4:]))

    def _write(self, bug: Bug, *, new: bool) -> None:
        path = self._path(bug.id)
        serialized = dump_bug(bug)
        if len(serialized) > 262_144:
            raise StoreError("Bug record exceeds the size limit")
        descriptor, name = tempfile.mkstemp(prefix=f".{bug.id}-", suffix=".tmp", dir=self._directory())
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            if new:
                # Atomically publish without overwriting an ID created by another writer.
                os.link(temporary, path)
            else:
                self._path(bug.id)  # Recheck that the destination has not become a symlink.
                os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def create(self, report: BugReport) -> Bug:
        report = BugReport.model_validate(report.model_dump())
        with self._lock():
            existing = self.list()
            number = max((int(bug.id[4:]) for bug in existing), default=0) + 1
            bug = Bug(id=f"BUG-{number:03d}", created_at=datetime.now(timezone.utc), report=report)
            self._write(bug, new=True)
        return bug

    def save(self, bug: Bug) -> Bug:
        bug = Bug.model_validate_json(bug.model_dump_json())
        with self._lock():
            current = self.get(bug.id)
            if bug.revision != current.revision:
                raise StoreError("Bug changed since it was read; reload before saving")
            if bug.created_at != current.created_at:
                raise StoreError("Bug creation time is immutable")
            if bug.status != current.status and bug.status not in TRANSITIONS[current.status]:
                raise StoreError(f"Invalid bug state transition: {current.status} -> {bug.status}")
            if current.status != "new" and bug.report != current.report:
                raise StoreError("Report cannot change after processing starts; create a new report")
            saved = bug.model_copy(update={"revision": current.revision + 1})
            self._write(saved, new=False)
        return saved
