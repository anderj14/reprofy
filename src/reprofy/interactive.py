"""Small dependency-free terminal home screen for the Reprofy CLI."""

from collections import Counter
from contextlib import redirect_stdout
import json
from io import StringIO
import os
from pathlib import Path
import select
import sys
from typing import Callable, Sequence

from .config import ConfigError, load_automation_mode, load_config
from .store import StoreError, YamlBugStore


RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
DIM = "\033[2m"


def _paint(text: str, color: str, enabled: bool) -> str:
    return f"{color}{text}{RESET}" if enabled else text


def _banner(color: bool) -> None:
    art = (
        "  ____                         __         \n"
        " |  _ \\ ___ _ __  _ __   ___  / _|_   _   \n"
        " | |_) / _ \\ '_ \\| '_ \\ / _ \\| |_| | | |  \n"
        " |  _ <  __/ |_) | |_) | (_) |  _| |_| |  \n"
        " |_| \\_\\___| .__/| .__/ \\___/|_|  \\__, |  \n"
        "             |_|   |_|               |___/   \n"
    )
    print(_paint(art, CYAN, color), end="")
    print(_paint("  From unclear report to reviewed fix", DIM, color))
    print()


def _project_status(project: Path, color: bool) -> None:
    print(_paint("╭─ CURRENT PROJECT", CYAN, color))
    print(f"│ {project}")
    if not (project / "reprofy.yaml").is_file():
        print("╰─ " + _paint("NOT INITIALIZED", YELLOW, color))
        return
    try:
        load_config(project)
        mode = load_automation_mode(project)
        bugs = YamlBugStore(project).list()
        counts = Counter(bug.status for bug in bugs)
        summary = ", ".join(f"{status} {counts[status]}" for status in ("new", "auto_running", "needs_human_review", "branch_ready", "fix_verified", "applied", "failed") if counts[status])
        mode_label = "AUTOMATIC" if mode == "automatic" else "MANUAL"
        mode_color = GREEN if mode == "automatic" else YELLOW
        print("│ " + _paint(f"MODE · {mode_label}", mode_color, color))
        print("╰─ " + _paint("READY", GREEN, color), f"· {len(bugs)} report(s)" + (f" · {summary}" if summary else ""))
    except (ConfigError, StoreError, OSError) as error:
        print("╰─ " + _paint(f"NEEDS ATTENTION · {error}", YELLOW, color))


def _initialize(project: Path, command_runner: Callable[[Sequence[str]], int], ask) -> None:
    source_default = "src" if (project / "src").is_dir() else "."
    tests_default = "tests" if (project / "tests").is_dir() else "."
    source_dir = ask(f"Python source directory [{source_default}]: ").strip() or source_default
    tests_dir = ask(f"Tests directory [{tests_default}]: ").strip() or tests_default
    backend = ask("Model provider [meta/baseten, default meta]: ").strip().lower() or "meta"
    if backend not in {"meta", "baseten"}:
        print("Choose meta or baseten.")
        return
    command: list[str] = ["init", "--project", str(project), "--tests-dir", tests_dir, "--backend", backend]
    for directory in source_dir.split(","):
        command.extend(["--source-dir", directory.strip()])
    command_runner(command)


def _new_report(project: Path, command_runner: Callable[[Sequence[str]], int], ask) -> None:
    text = ask("Describe the bug: ").strip()
    if not text:
        print("Report cancelled: the description is empty.")
        return
    expected = ask("What should happen instead? [optional]: ").strip()
    actual = ask("What happened? [optional]: ").strip()
    context = ask("Extra context [optional]: ").strip()
    command: list[str] = ["report", text, "--project", str(project)]
    if expected:
        command.extend(["--expected", expected])
    if actual:
        command.extend(["--actual", actual])
    if context:
        command.extend(["--context", context])
    command_runner(command)


def _review_apply(project: Path, command_runner: Callable[[Sequence[str]], int], ask) -> None:
    bug_id = ask("Bug ID or apply.json path: ").strip()
    if not bug_id:
        print("Apply cancelled.")
        return
    check = command_runner(["apply", bug_id, "--project", str(project), "--check"])
    if check != 0:
        print("Preview failed; no application was attempted.")
        return
    confirm = ask("Preview passed. Open the approval prompt and review the diff now? [y/N]: ").strip().lower()
    if confirm == "y":
        command_runner(["apply", bug_id, "--project", str(project)])
    else:
        print("No files changed. You can review this proposal later.")


def _delete_resolved(project: Path, command_runner: Callable[[Sequence[str]], int], ask) -> None:
    try:
        resolved = [bug for bug in YamlBugStore(project).list() if bug.status == "applied"]
    except (StoreError, OSError) as error:
        print(f"Could not load resolved reports: {error}")
        return
    if not resolved:
        print("There are no applied bug reports to delete.")
        return
    print("\nResolved reports (applied fixes):")
    for bug in resolved:
        title = " ".join(bug.report.text.split())[:90]
        print(f"  {bug.id:<12} {title}")
    bug_id = ask("Bug ID to delete (Enter to cancel): ").strip().upper()
    if not bug_id:
        print("Deletion cancelled.")
        return
    if bug_id not in {bug.id for bug in resolved}:
        print("Choose an ID from the resolved reports above.")
        return
    command_runner(["delete", bug_id, "--project", str(project)])


def _retry_report(project: Path, command_runner: Callable[[Sequence[str]], int], ask) -> None:
    try:
        waiting = [bug for bug in YamlBugStore(project).list() if bug.status == "needs_human_review"]
    except (StoreError, OSError) as error:
        print(f"Could not load reports awaiting review: {error}")
        return
    if not waiting:
        print("There are no reports awaiting human review.")
        return
    print("\nReports awaiting human review:")
    for bug in waiting:
        title = " ".join(bug.report.text.split())[:90]
        print(f"  {bug.id:<12} {title}")
    bug_id = ask("Bug ID to requeue (Enter to cancel): ").strip().upper()
    if not bug_id:
        print("Retry cancelled.")
        return
    if bug_id not in {bug.id for bug in waiting}:
        print("Choose an ID from the reports above.")
        return
    command_runner(["retry", bug_id, "--project", str(project)])


def _read_worker_events(project: Path, *, limit: int = 200) -> list[dict[str, str]]:
    path = project / ".reprofy/worker-events.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            events.append({
                "recorded_at": str(value.get("recorded_at", "")),
                "bug_id": str(value.get("bug_id", "")),
                "message": " ".join(str(value.get("message", "")).split())[:300],
            })
    return events


def _show_activity(project: Path) -> None:
    events = _read_worker_events(project)
    print("\nAUTOMATION ACTIVITY · recent worker report")
    if not events:
        print("  No automatic activity recorded yet.")
        return
    for item in events:
        stamp = item["recorded_at"]
        try:
            stamp = stamp[11:19] if "T" in stamp else stamp[:19]
        except (IndexError, TypeError):
            stamp = ""
        print(f"  {stamp}  {item['bug_id']:<10} {item['message']}")


def _home_view(project: Path, color: bool) -> str:
    view = StringIO()
    with redirect_stdout(view):
        _project_status(project, color)
        print()
        if not (project / "reprofy.yaml").is_file():
            print(_paint("  GET STARTED", CYAN, color))
            print("  [1] Initialize this project")
            print("  [p] Choose another project folder")
            print("  [0] Exit")
        else:
            events = _read_worker_events(project, limit=6)
            if events:
                print(_paint("  AUTOMATION ACTIVITY", CYAN, color))
                for item in reversed(events):
                    stamp = item["recorded_at"]
                    stamp = stamp[11:16] if "T" in stamp else stamp[:5]
                    print(f"  {stamp}  {item['bug_id']:<9} {item['message'][:90]}")
                print("  [10] View activity report")
            print(_paint("  REPORTS", CYAN, color))
            print("  [1] Report a bug")
            print("  [2] List bug reports")
            print("  [3] Inspect a bug")
            print(_paint("  FIX WORKFLOW", CYAN, color))
            print("  [4] Generate a fix proposal manually")
            print("  [5] Review and apply a proposal")
            print(_paint("  PROJECT", CYAN, color))
            print("  [6] Show project configuration")
            print("  [7] Delete a resolved bug report")
            mode = load_automation_mode(project)
            next_mode = "manual" if mode == "automatic" else "automatic"
            print(f"  [8] Switch to {next_mode} mode")
            print("  [9] Retry a report awaiting human review")
            print("  [p] Choose another project folder")
            print("  [0] Exit")
    return view.getvalue()


def _live_menu_choice(initial_view: str, refresh_view: Callable[[], str]) -> str:
    """Read one menu choice while refreshing the view when report state changes."""
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    original_settings = termios.tcgetattr(descriptor)
    buffer = ""
    view = initial_view
    try:
        tty.setcbreak(descriptor)
        live_settings = termios.tcgetattr(descriptor)
        live_settings[3] &= ~termios.ECHO
        termios.tcsetattr(descriptor, termios.TCSADRAIN, live_settings)
        sys.stdout.write(view)
        sys.stdout.write("Choose an action: ")
        sys.stdout.flush()
        while True:
            ready, _, _ = select.select([descriptor], [], [], 1.0)
            if not ready:
                updated = refresh_view()
                if updated != view:
                    line_count = view.count("\n")
                    sys.stdout.write(f"\033[{line_count}A\r\033[J")
                    view = updated
                    sys.stdout.write(view)
                    sys.stdout.write("Choose an action: " + buffer)
                    sys.stdout.flush()
                continue
            character = os.read(descriptor, 1).decode("utf-8", errors="ignore")
            if character in {"\r", "\n"}:
                sys.stdout.write("\n")
                sys.stdout.flush()
                return buffer
            if character == "\x03":
                raise KeyboardInterrupt
            if character == "\x04":
                sys.stdout.write("\n")
                return "0"
            if character in {"\x7f", "\b"}:
                buffer = buffer[:-1]
            elif character.isprintable():
                buffer += character
            sys.stdout.write("\r\033[KChoose an action: " + buffer)
            sys.stdout.flush()
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, original_settings)


def run_interactive(
    command_runner: Callable[[Sequence[str]], int], *, project: Path | None = None,
    input_fn: Callable[[str], str] | None = None, output_is_terminal: bool | None = None,
) -> int:
    """Run the interactive home screen, delegating actions to the regular CLI."""
    current_project = Path.cwd() if project is None else project
    current_project = current_project.expanduser().resolve()
    ask = input if input_fn is None else input_fn
    color = sys.stdout.isatty() if output_is_terminal is None else output_is_terminal

    _banner(color)
    while True:
        try:
            live = input_fn is None and sys.stdin.isatty() and sys.stdout.isatty()
            initial_view = _home_view(current_project, color)
            if live:
                choice = _live_menu_choice(
                    initial_view,
                    lambda: _home_view(current_project, color),
                ).strip().lower()
            else:
                print(initial_view, end="")
                choice = ask("Choose an action: ").strip().lower()
            if choice in {"0", "q", "quit", "exit"}:
                print("Goodbye.")
                return 0
            if choice == "p":
                folder = ask("Project folder: ").strip()
                if folder:
                    current_project = Path(folder).expanduser().resolve()
                continue
            if not (current_project / "reprofy.yaml").is_file():
                if choice == "1":
                    _initialize(current_project, command_runner, ask)
                else:
                    print("Choose 1, p, or 0.")
                continue
            if choice == "1":
                _new_report(current_project, command_runner, ask)
            elif choice == "2":
                command_runner(["list", "--project", str(current_project)])
            elif choice == "3":
                bug_id = ask("Bug ID: ").strip()
                if bug_id:
                    command_runner(["show", bug_id, "--project", str(current_project)])
            elif choice == "4":
                bug_id = ask("Bug ID to fix: ").strip()
                if bug_id:
                    command_runner(["fix", bug_id, "--project", str(current_project)])
            elif choice == "5":
                _review_apply(current_project, command_runner, ask)
            elif choice == "6":
                command_runner(["config", "--project", str(current_project)])
            elif choice == "7":
                _delete_resolved(current_project, command_runner, ask)
            elif choice == "8":
                current_mode = load_automation_mode(current_project)
                next_mode = "manual" if current_mode == "automatic" else "automatic"
                if next_mode == "automatic":
                    confirm = ask("Automatic mode lets the worker use model credits and prepare branches. Enable it? [y/N]: ").strip().lower()
                    if confirm != "y":
                        print("Mode unchanged.")
                        continue
                command_runner(["mode", next_mode, "--project", str(current_project)])
            elif choice == "9":
                _retry_report(current_project, command_runner, ask)
            elif choice == "10":
                _show_activity(current_project)
            else:
                print("Choose a listed number, p, or 0.")
            print()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return 0
