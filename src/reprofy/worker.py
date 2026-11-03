"""Poll new reports and prepare verified fixes on isolated Git branches."""

import argparse
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from .applying import apply_proposal, load_apply_proposal
from .backends import build_step_backends
from .bugs import Bug, BugFix
from .config import ConfigError, load_config
from .engine import fix_bug
from .post_apply import run_applied_suite, test_run_passed
from .store import StoreError, YamlBugStore, dump_bug


class WorkerError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        timeout=30, check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise WorkerError(f"git {' '.join(args)} failed: {detail[:1500]}")
    return result.stdout.strip()


def _review(store: YamlBugStore, bug_id: str, message: str) -> None:
    current = store.get(bug_id)
    if current.status == "auto_running":
        store.save(current.model_copy(update={
            "status": "needs_human_review", "failure": message[:4000],
        }))


def _load_project_bug(worktree_project: Path, bug: Bug) -> None:
    bug_directory = worktree_project / ".reprofy/bugs"
    bug_directory.mkdir(parents=True, exist_ok=True)
    branch_copy = bug.model_copy(update={"status": "new", "failure": None})
    (bug_directory / f"{bug.id}.yaml").write_text(dump_bug(branch_copy), encoding="utf-8")


def _clean_git_root(root: Path) -> None:
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise WorkerError("Git working tree has uncommitted changes; commit or stash them before automatic work")


def _create_worktree(project: Path, bug_id: str) -> tuple[Path, Path, Path, str]:
    root = Path(_git(project, "rev-parse", "--show-toplevel")).resolve()
    _clean_git_root(root)
    base = os.environ.get("REPROFY_BASE_BRANCH", "").strip() or _git(root, "branch", "--show-current")
    if not base:
        raise WorkerError("Cannot determine the base branch from a detached HEAD")
    _git(root, "rev-parse", "--verify", f"refs/heads/{base}")
    branch = f"reprofy/{bug_id}"
    existing = subprocess.run(["git", "-C", str(root), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0
    if existing:
        raise WorkerError(f"Branch already exists: {branch}; inspect or remove it before retrying")
    worktree = root / ".reprofy/worktrees" / bug_id
    if worktree.exists():
        raise WorkerError(f"Worktree path already exists: {worktree}")
    worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(root, "worktree", "add", "-b", branch, str(worktree), base)
    relative_project = project.resolve().relative_to(root)
    worktree_project = (worktree / relative_project).resolve()
    if not (worktree_project / "reprofy.yaml").is_file():
        raise WorkerError("The configured Reprofy project is not present in the new worktree")
    return root, worktree, worktree_project, branch


def process_report(project: Path, bug_id: str, *, max_attempts: int = 10) -> str:
    project = project.resolve()
    store = YamlBugStore(project)
    bug = store.get(bug_id)
    if bug.status != "new":
        return f"{bug_id}: skipped ({bug.status})"
    claimed = store.save(bug.model_copy(update={"status": "auto_running", "failure": None}))
    worktree: Path | None = None
    backends = None
    try:
        root, worktree, worktree_project, branch = _create_worktree(project, bug_id)
        _load_project_bug(worktree_project, claimed)
        config = load_config(worktree_project).model_copy(update={"max_attempts": max_attempts})
        source_usage = project / ".reprofy/usage.jsonl"
        source_usage.parent.mkdir(parents=True, exist_ok=True)
        source_usage.touch(exist_ok=True)
        branch_usage = worktree_project / ".reprofy/usage.jsonl"
        branch_usage.parent.mkdir(parents=True, exist_ok=True)
        branch_usage.symlink_to(source_usage)
        env_file = project / ".env"
        run_id = uuid4().hex
        backends = build_step_backends(
            config, worktree_project, bug_id=bug_id, run_id=run_id,
            env_file=env_file if env_file.is_file() else None,
        )
        result = fix_bug(
            worktree_project, bug_id, backends, config=config, run_id=run_id,
            on_event=lambda message: print(message, flush=True),
        )
        if result.status != "fix_verified" or not result.apply_file:
            reason = result.error or f"Automation stopped with outcome {result.status}; inspect {result.artifact_dir}"
            _review(store, bug_id, reason)
            return f"{bug_id}: needs_human_review · {reason}"

        proposal = load_apply_proposal(Path(result.apply_file))
        applied = apply_proposal(worktree_project, proposal, approve=lambda _diff: True)
        if applied.status != "applied":
            raise WorkerError(f"Could not apply verified patch inside worktree: {applied.status}")
        post_run = run_applied_suite(worktree_project, config)
        if not test_run_passed(post_run):
            raise WorkerError(f"Branch failed the post-apply suite:\n{post_run.output[:3500]}")

        changed_paths = [change.path for change in proposal.changes]
        if not changed_paths:
            raise WorkerError("Verified proposal contains no files to commit")
        _git(worktree, "add", "--", *changed_paths)
        _git(worktree, "commit", "-m", f"Fix {bug_id}: verified report resolution")
        commit_sha = _git(worktree, "rev-parse", "HEAD")

        branch_bug = YamlBugStore(worktree_project).get(bug_id)
        current = store.get(bug_id)
        fix = (branch_bug.fix or BugFix()).model_copy(update={
            "branch_name": branch, "commit_sha": commit_sha,
            "tests_pass": True,
        })
        store.save(current.model_copy(update={
            "status": "branch_ready", "classification": branch_bug.classification,
            "repro": branch_bug.repro, "fix": fix, "failure": None,
        }))
        return f"{bug_id}: branch_ready · {branch} · {commit_sha[:12]} · worktree {worktree}"
    except Exception as error:
        _review(store, bug_id, str(error))
        return f"{bug_id}: needs_human_review · {error}"
    finally:
        if backends is not None:
            backends.close()


class AutoWorker:
    def __init__(self, project: Path, *, poll_seconds: float = 3, max_attempts: int = 10):
        self.project = project.resolve()
        self.poll_seconds = max(1.0, poll_seconds)
        self.max_attempts = min(10, max(1, max_attempts))
        self.store = YamlBugStore(self.project)
        self.lock_path = self.project / ".reprofy/auto-worker.lock"

    def run_once(self) -> str | None:
        with self.lock_path.open("a+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "Another Reprofy worker is already processing this project."
            for bug in self.store.list():
                if bug.status == "auto_running":
                    self.store.save(bug.model_copy(update={
                        "status": "needs_human_review",
                        "failure": "Worker stopped before recording a result; inspect its worktree and artifacts.",
                    }))
            bug = next((item for item in self.store.list() if item.status == "new"), None)
            return process_report(self.project, bug.id, max_attempts=self.max_attempts) if bug else None

    def run_forever(self) -> None:
        print(f"Reprofy worker watching {self.project}", flush=True)
        while True:
            try:
                result = self.run_once()
                if result:
                    print(result, flush=True)
                if result is None:
                    time.sleep(self.poll_seconds)
            except KeyboardInterrupt:
                print("Worker stopped.", flush=True)
                return
            except Exception as error:
                print(f"Worker error: {error}", file=sys.stderr, flush=True)
                time.sleep(self.poll_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reprofy worker")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--poll-seconds", type=float, default=float(os.environ.get("REPROFY_POLL_SECONDS", "3")))
    parser.add_argument("--max-attempts", type=int, default=int(os.environ.get("REPROFY_MAX_AUTO_ATTEMPTS", "10")))
    parser.add_argument("--once", action="store_true", help="Process at most one pending report and exit")
    args = parser.parse_args(argv)
    try:
        worker = AutoWorker(args.project, poll_seconds=args.poll_seconds, max_attempts=args.max_attempts)
        if args.once:
            print(worker.run_once() or "No new reports.")
        else:
            worker.run_forever()
        return 0
    except (ConfigError, StoreError, OSError, ValueError) as error:
        print(f"reprofy worker: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
