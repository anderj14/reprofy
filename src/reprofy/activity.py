"""Human-readable, detachable view of the worker's local activity stream."""

from datetime import datetime
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

from .config import load_automation_mode
from .store import YamlBugStore


ACTIVE_STATUSES = {"new", "auto_running"}


def _decode_event(line: bytes, bug_id: str | None) -> dict[str, str] | None:
    try:
        item = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(item, dict):
        return None
    current_id = str(item.get("bug_id", ""))
    if bug_id and current_id != bug_id:
        return None
    return {
        "recorded_at": str(item.get("recorded_at", "")),
        "bug_id": current_id,
        "message": str(item.get("message", "")),
    }


def _print_event(item: dict[str, str]) -> None:
    stamp = item["recorded_at"]
    try:
        local_time = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
        stamp = local_time.strftime("%H:%M:%S")
    except (ValueError, TypeError):
        stamp = stamp[:19]
    lines = item["message"].splitlines() or [""]
    print(f"{stamp}  {item['bug_id']}  {lines[0]}", flush=True)
    for line in lines[1:]:
        print(f"           {line}", flush=True)


def show_activity(project: Path, *, bug_id: str | None = None, follow: bool = False) -> None:
    project = project.expanduser().resolve()
    store = YamlBugStore(project)
    if bug_id:
        bug_id = bug_id.strip().upper()
        bug = store.get(bug_id)
        print(f"AUTOMATION ACTIVITY · {bug.id} · {bug.status}")
    else:
        bug = None
        print("AUTOMATION ACTIVITY · all reports")
    print("Code and test output are shown as the worker records each step.")
    if follow:
        print("This is only an activity viewer. Ctrl+C closes it; the worker keeps running.\n")
    event_path = project / ".reprofy/worker-events.jsonl"
    offset = 0
    if event_path.exists():
        with event_path.open("rb") as stream:
            while line := stream.readline():
                offset = stream.tell()
                item = _decode_event(line, bug_id)
                if item:
                    _print_event(item)
    if not follow:
        if not event_path.exists() or offset == 0:
            print("No activity has been recorded yet.")
        return

    def is_active() -> bool:
        if bug_id:
            status = store.get(bug_id).status
            return status in ACTIVE_STATUSES and not (
                status == "new" and load_automation_mode(project) == "manual"
            )
        mode = load_automation_mode(project)
        return any(
            item.status == "auto_running"
            or (item.status == "new" and mode == "automatic")
            for item in store.list()
        )

    if not is_active():
        print("\nNo report is currently queued or running.")
        return
    print("\nWatching for new activity…\n", flush=True)
    while True:
        if event_path.exists():
            size = event_path.stat().st_size
            if size < offset:
                offset = 0
            with event_path.open("rb") as stream:
                stream.seek(offset)
                while line := stream.readline():
                    offset = stream.tell()
                    item = _decode_event(line, bug_id)
                    if item:
                        _print_event(item)
        if not is_active():
            print("\nAutomation finished. The worker is unaffected by this viewer.")
            return
        time.sleep(0.5)


def open_activity_window(project: Path, bug_id: str | None = None) -> bool:
    """Open Terminal.app on macOS; return False so callers can print a fallback command."""
    if sys.platform != "darwin" or shutil.which("open") is None:
        return False
    project = project.expanduser().resolve()
    launcher_dir = project / ".reprofy/activity-windows"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    label = bug_id or "all-reports"
    launcher = launcher_dir / f"{label}-{time.time_ns()}.command"
    repository_root = Path(__file__).resolve().parents[2]
    local_python = repository_root / ".venv/bin/python"
    filters = f"--bug {shlex.quote(bug_id)} " if bug_id else ""
    if local_python.is_file():
        command = (
            f"{shlex.quote(str(local_python))} -m reprofy activity "
            f"--project {shlex.quote(str(project))} {filters}--follow"
        )
    else:
        command = (
            f"reprofy activity --project {shlex.quote(str(project))} "
            f"{filters}--follow"
        )
    launcher.write_text(
        "#!/bin/zsh\n"
        f"cd {shlex.quote(str(project))}\n"
        f"{command}\n"
        "echo\n"
        "echo 'Activity view closed. The Reprofy worker continues independently.'\n"
        "echo 'Press Enter to close this terminal window.'\n"
        "read reply\n",
        encoding="utf-8",
    )
    launcher.chmod(0o700)
    result = subprocess.run(
        ["open", "-a", "Terminal", str(launcher)],
        capture_output=True, text=True, check=False, timeout=15,
    )
    if result.returncode:
        launcher.unlink(missing_ok=True)
        return False
    return True

