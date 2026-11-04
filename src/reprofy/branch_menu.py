"""Local review helpers for Reprofy's isolated worker branches."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess

from .store import StoreError, YamlBugStore


@dataclass(frozen=True)
class PreparedBranch:
    bug_id: str
    path: Path
    commit: str
    summary: str
    status: str
    published: bool = False
    pull_request_url: str | None = None
    pull_request_number: int | None = None


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    title: str
    state: str
    mergeable: str
    merge_state: str
    review_decision: str
    base_branch: str
    head_sha: str


def list_prepared_branches(project: Path) -> list[PreparedBranch]:
    project = project.expanduser().resolve()
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or "Git could not list the project's worktrees."
        raise RuntimeError(detail)

    root_result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    if root_result.returncode:
        detail = root_result.stderr.strip() or "Git could not locate the repository root."
        raise RuntimeError(detail)
    repository_root = Path(root_result.stdout.strip()).resolve()
    expected_root = (repository_root / ".reprofy/worktrees").resolve()
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in (*result.stdout.splitlines(), ""):
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value

    try:
        store = YamlBugStore(project)
    except (StoreError, OSError):
        store = None

    branches = []
    for record in records:
        branch_ref = record.get("branch", "")
        if not branch_ref.startswith("refs/heads/reprofy/") or not record.get("worktree"):
            continue
        bug_id = branch_ref.removeprefix("refs/heads/reprofy/")
        if not re.fullmatch(r"BUG-[0-9]{3,}", bug_id):
            continue
        path = Path(record["worktree"]).resolve()
        if not path.is_relative_to(expected_root) or not path.is_dir():
            continue
        commit = record.get("HEAD", "")[:7]
        summary_result = subprocess.run(
            ["git", "-C", str(path), "log", "-1", "--format=%s"],
            capture_output=True,
            text=True,
            check=False,
        )
        summary = summary_result.stdout.strip() if summary_result.returncode == 0 else "Commit details unavailable"
        try:
            status = store.get(bug_id).status if store else "unknown"
        except StoreError:
            status = "unknown"
        fix = None
        if store:
            try:
                fix = store.get(bug_id).fix
            except StoreError:
                pass
        upstream = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            capture_output=True,
            text=True,
            check=False,
        )
        branches.append(PreparedBranch(
            bug_id, path, commit, summary, status,
            published=bool(fix and fix.branch_published) or upstream.returncode == 0,
            pull_request_url=fix.pull_request_url if fix else None,
            pull_request_number=fix.pull_request_number if fix else None,
        ))
    return sorted(branches, key=lambda item: int(item.bug_id[4:]))


def open_branch_in_vscode(path: Path) -> bool:
    path = path.expanduser().resolve()
    if not path.is_dir():
        return False
    code = shutil.which("code")
    if code:
        try:
            subprocess.Popen([code, "--new-window", str(path)])
            return True
        except OSError:
            return False
    open_command = shutil.which("open")
    if open_command:
        result = subprocess.run(
            [open_command, "-na", "Visual Studio Code", "--args", "--new-window", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    return False


def get_branch_diff(path: Path) -> str:
    result = subprocess.run(
        [
            "git", "-C", str(path), "show", "--no-ext-diff", "--stat", "--patch",
            "--unified=4", "--format=medium", "HEAD",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or "Git could not load the branch changes."
        raise RuntimeError(detail)
    return result.stdout.strip()


def _run(command: list[str], *, cwd: Path, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{command[0]} did not respond within {timeout} seconds") from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"Command failed: {command[0]}")
    return result.stdout.strip()


def _save_fix(project: Path, bug_id: str, **updates) -> None:
    store = YamlBugStore(project)
    bug = store.get(bug_id)
    if bug.fix is None:
        raise RuntimeError(f"{bug_id} has no verified fix record")
    store.save(bug.model_copy(update={"fix": bug.fix.model_copy(update=updates)}))


def _verified_branch_bug(project: Path, branch: PreparedBranch):
    bug = YamlBugStore(project).get(branch.bug_id)
    if bug.status != "branch_ready" or bug.fix is None or not bug.fix.commit_sha:
        raise RuntimeError("Only a verified branch ready for review can be published or merged")
    head = _run(["git", "rev-parse", "HEAD"], cwd=branch.path)
    if head != bug.fix.commit_sha:
        raise RuntimeError("This branch changed after Reprofy verified it; review and verify the new changes first")
    dirty = _run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=branch.path)
    if dirty:
        raise RuntimeError("This branch has uncommitted changes; verify them before publishing")
    return bug


def publish_branch(project: Path, branch: PreparedBranch) -> str:
    _verified_branch_bug(project, branch)
    branch_name = f"reprofy/{branch.bug_id}"
    output = _run(
        ["git", "push", "--set-upstream", "origin", branch_name],
        cwd=branch.path,
    )
    _save_fix(project, branch.bug_id, branch_published=True)
    return output or f"{branch_name} is up to date on origin."


def _default_base(project: Path, bug_id: str) -> str:
    bug = YamlBugStore(project).get(bug_id)
    if bug.fix and bug.fix.base_branch:
        return bug.fix.base_branch
    root = Path(_run(["git", "rev-parse", "--show-toplevel"], cwd=project)).resolve()
    remote_head = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if remote_head.returncode == 0:
        return remote_head.stdout.strip().removeprefix("origin/")
    current = _run(["git", "branch", "--show-current"], cwd=root)
    if not current or current.startswith("reprofy/"):
        raise RuntimeError("Could not determine the project's base branch for the Pull Request")
    return current


def _require_gh() -> str:
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("GitHub connection tool 'gh' is not installed")
    return gh


def _parse_pull_request(data: dict) -> PullRequest:
    return PullRequest(
        number=int(data["number"]),
        url=str(data["url"]),
        title=str(data.get("title", "")),
        state=str(data.get("state", "UNKNOWN")),
        mergeable=str(data.get("mergeable", "UNKNOWN")),
        merge_state=str(data.get("mergeStateStatus", "UNKNOWN")),
        review_decision=str(data.get("reviewDecision") or "REVIEW_NOT_REQUIRED"),
        base_branch=str(data.get("baseRefName", "")),
        head_sha=str(data.get("headRefOid", "")),
    )


def _mark_merged_and_sync(project: Path, bug_id: str, base: str, url: str) -> str:
    store = YamlBugStore(project)
    current = store.get(bug_id)
    if current.status == "branch_ready":
        store.save(current.model_copy(update={"status": "applied"}))
    root = Path(_run(["git", "rev-parse", "--show-toplevel"], cwd=project)).resolve()
    current_branch = _run(["git", "branch", "--show-current"], cwd=root)
    if current_branch != base:
        return f"Pull Request merged and BUG marked resolved. Open local branch {base} to sync: {url}"
    dirty = _run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root)
    if dirty:
        return f"Pull Request merged and BUG marked resolved. Save local changes before syncing {base}: {url}"
    try:
        _run(["git", "fetch", "origin", base], cwd=root)
        merge = subprocess.run(
            ["git", "merge", "--no-edit", f"origin/{base}"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if merge.returncode:
            subprocess.run(
                ["git", "merge", "--abort"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            detail = (merge.stderr or merge.stdout).strip()
            return f"Pull Request merged and BUG marked resolved. Local update was aborted: {detail}"
    except RuntimeError as error:
        return f"Pull Request merged and BUG marked resolved. Local refresh needs attention: {error}"
    return f"Pull Request merged, BUG marked resolved, and local {base} updated: {url}"


def get_pull_request(branch: PreparedBranch) -> PullRequest:
    gh = _require_gh()
    branch_name = f"reprofy/{branch.bug_id}"
    output = _run(
        [
            gh, "pr", "view", branch_name, "--json",
            "number,url,title,state,mergeable,mergeStateStatus,reviewDecision,baseRefName,headRefOid",
        ],
        cwd=branch.path,
    )
    try:
        return _parse_pull_request(json.loads(output))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("GitHub returned incomplete Pull Request details") from error


def create_pull_request(project: Path, branch: PreparedBranch) -> PullRequest:
    bug = _verified_branch_bug(project, branch)
    if not branch.published:
        raise RuntimeError("Publish this branch before creating its Pull Request")
    gh = _require_gh()
    branch_name = f"reprofy/{branch.bug_id}"
    existing = _run(
        [gh, "pr", "list", "--head", branch_name, "--state", "open", "--limit", "1", "--json", "number,url,title"],
        cwd=branch.path,
    )
    try:
        found = json.loads(existing)
    except json.JSONDecodeError as error:
        raise RuntimeError("GitHub returned invalid Pull Request data") from error
    if found:
        info = _parse_pull_request({**found[0], "state": "OPEN"})
    else:
        report_text = " ".join(bug.report.text.split())
        title = f"Fix {bug.id}: {report_text}"[:240]
        expected = bug.expected_behavior or bug.report.expected or "Resolve the behavior described in the report."
        body = (
            "## Bug report\n"
            f"{bug.report.text}\n\n"
            "## Expected behavior\n"
            f"{expected}\n\n"
            "## Verification\n"
            "Reprofy generated a regression test, verified the fix against it and ran the project test suite in an isolated worktree.\n"
        )
        base = _default_base(project, branch.bug_id)
        _run(
            [
                gh, "pr", "create", "--head", branch_name, "--base", base,
                "--title", title, "--body", body,
            ],
            cwd=branch.path,
        )
        info = get_pull_request(branch)
    _save_fix(
        project, branch.bug_id,
        branch_published=True,
        pull_request_url=info.url,
        pull_request_number=info.number,
    )
    return info


def merge_pull_request(project: Path, branch: PreparedBranch, method: str) -> str:
    if method not in {"squash", "merge", "rebase"}:
        raise ValueError("Choose squash, merge, or rebase")
    bug = _verified_branch_bug(project, branch)
    info = get_pull_request(branch)
    if info.state == "MERGED":
        return _mark_merged_and_sync(project, branch.bug_id, info.base_branch, info.url)
    if info.state != "OPEN":
        raise RuntimeError(f"Pull Request is {info.state.lower()}, not open")
    if info.mergeable == "CONFLICTING":
        raise RuntimeError("GitHub reports conflicts with the target branch; update and review the branch first")
    expected_sha = bug.fix.commit_sha if bug.fix else None
    if expected_sha and info.head_sha != expected_sha:
        raise RuntimeError("The published branch changed after Reprofy's verification; review the new diff before merging")

    root = Path(_run(["git", "rev-parse", "--show-toplevel"], cwd=project)).resolve()
    base = info.base_branch
    current_branch = _run(["git", "branch", "--show-current"], cwd=root)
    if current_branch != base:
        raise RuntimeError(f"The local project is on {current_branch or 'detached HEAD'}, while the Pull Request targets {base}")
    dirty = _run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root)
    if dirty:
        raise RuntimeError("Save or commit the local project changes before merging so Reprofy can safely update it")

    gh = _require_gh()
    sha = info.head_sha or expected_sha or branch.commit
    _run(
        [gh, "pr", "merge", f"reprofy/{branch.bug_id}", f"--{method}", "--match-head-commit", sha],
        cwd=branch.path,
    )
    return _mark_merged_and_sync(project, branch.bug_id, base, info.url)
