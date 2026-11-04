"""Human-readable, detachable view of the worker's local activity stream."""

from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

from .config import load_automation_mode
from .store import YamlBugStore


ACTIVE_STATUSES = {"new", "auto_running"}
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"


def _colors_enabled() -> bool:
    return (
        hasattr(sys.stdout, "isatty")
        and sys.stdout.isatty()
        and "NO_COLOR" not in os.environ
        and os.environ.get("TERM") != "dumb"
    )


def _paint(text: str, *styles: str) -> str:
    if not _colors_enabled() or not styles:
        return text
    return f"{''.join(styles)}{text}{RESET}"


def _event_style(message: str) -> tuple[str, ...]:
    folded = message.casefold()
    if any(word in folded for word in (
        "failed", "falló", "fallo", "error", "needs_human_review", "revisión humana", "se detuvo",
    )):
        return RED, BOLD
    if any(word in folded for word in (
        "branch_ready", "listo para revisión", "tests passed", "pruebas aprobadas",
        "applied", "aplicado", "reproduction accepted", "reproducción aceptada",
    )):
        return GREEN, BOLD
    if any(word in folded for word in ("running", "generating", "worker tomó", "attempt ", "validating", "revisando", "reintentando")):
        return YELLOW
    return BLUE


def _detail_style(line: str) -> tuple[str, ...]:
    stripped = line.lstrip()
    if stripped.startswith(("--- a/", "+++ b/", "@@")):
        return CYAN, BOLD
    if stripped.startswith("+") and not stripped.startswith("+++"):
        return GREEN
    if stripped.startswith("-") and not stripped.startswith("---"):
        return RED
    folded = stripped.casefold()
    if any(word in folded for word in ("failed", "error", "traceback", "assertionerror", "e   ")):
        return RED
    if any(word in folded for word in ("passed", "success", "applied", "tests passed")):
        return GREEN
    if stripped.startswith((">", "E ")):
        return YELLOW
    if stripped.startswith(("def ", "class ", "from ", "import ")):
        return CYAN
    if stripped.startswith("assert "):
        return YELLOW
    if stripped.startswith(("if ", "elif ", "else:", "for ", "with ", "return ")):
        return BLUE
    if stripped.startswith(("GENERATED REPRODUCTION", "CANDIDATE SOURCE PATCH", "REGRESSION TEST OUTPUT", "FULL TEST SUITE OUTPUT", "POST-APPLY TEST OUTPUT")):
        return CYAN, BOLD
    return ()


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
    style = _event_style(lines[0])
    print(
        f"{_paint(stamp, DIM)}  {_paint(item['bug_id'], MAGENTA, BOLD)}  {_paint(lines[0], *style)}",
        flush=True,
    )
    for line in lines[1:]:
        print(f"           {_paint(line, *_detail_style(line))}", flush=True)


def show_activity(project: Path, *, bug_id: str | None = None, follow: bool = False) -> None:
    project = project.expanduser().resolve()
    store = YamlBugStore(project)
    if bug_id:
        bug_id = bug_id.strip().upper()
        bug = store.get(bug_id)
        title = _paint("AUTOMATION ACTIVITY", CYAN, BOLD)
        print(title + f" · {_paint(bug.id, MAGENTA, BOLD)} · {_paint(bug.status, *_event_style(bug.status))}")
    else:
        bug = None
        print(_paint("AUTOMATION ACTIVITY", CYAN, BOLD) + f" · {_paint('all reports', MAGENTA, BOLD)}")
    print(_paint("Code and test output are shown as the worker records each step.", DIM))
    if follow:
        print(_paint("This is only an activity viewer. Ctrl+C closes it; the worker keeps running.\n", YELLOW))
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
        print("\n" + _paint("No report is currently queued or running.", DIM))
        return
    print("\n" + _paint("Watching for new activity…", GREEN, BOLD) + "\n", flush=True)
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
            finished = _paint("Automation finished.", GREEN, BOLD)
            independent = _paint("The worker is unaffected by this viewer.", DIM)
            print("\n" + finished + " " + independent)
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

