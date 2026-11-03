import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Sequence

import xml.etree.ElementTree as ET

@dataclass(frozen=True)
class TestRun:
    returncode: int
    timed_out: bool
    output: str
    junit_xml: str | None


def run_pytest(
    workspace: Path,
    targets: Sequence[str],
    *,
    timeout_seconds: float = 30,
    test_command: Sequence[str] = ("{python}", "-m", "pytest"),
) -> TestRun:
    if timeout_seconds <= 0:
        raise ValueError("Test timeout must be positive")
    if list(test_command[:3]) != ["{python}", "-m", "pytest"]:
        raise ValueError("Only ['{python}', '-m', 'pytest', ...] test commands are supported")

    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    with TemporaryDirectory(prefix="reprofy-results-") as directory:
        report = Path(directory) / "report.xml"
        output = Path(directory) / "output.txt"
        command = [
            sys.executable, *test_command[1:],
            *targets,
            "-q", f"--junitxml={report}",
        ]
        timed_out = False

        with output.open("wb") as stream:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=(os.name == "posix"),
            )
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait()

        with output.open(encoding="utf-8", errors="replace") as stream:
            captured = stream.read(20_000)

        return TestRun(
            returncode=process.returncode,
            timed_out=timed_out,
            output=captured,
            junit_xml=report.read_text(encoding="utf-8")
            if report.exists() else None,
        )

@dataclass(frozen=True)
class TestSummary:
    status: str
    details: tuple[str, ...] = ()


def summarize_test_run(run: TestRun) -> TestSummary:
    if run.timed_out:
        return TestSummary("timeout")
    if run.junit_xml is None:
        return TestSummary("invalid", ("Missing pytest XML report",))

    try:
        root = ET.fromstring(run.junit_xml)
    except ET.ParseError:
        return TestSummary("invalid", ("Malformed pytest XML report",))

    if root.tag not in {"testsuites", "testsuite"}:
        return TestSummary("invalid", ("Unexpected XML root",))

    errors = list(root.iter("error"))
    failures = list(root.iter("failure"))
    cases = list(root.iter("testcase"))
    details = tuple(
        ET.tostring(node, encoding="unicode")
        for node in errors + failures
    )

    if errors or run.returncode not in {0, 1}:
        return TestSummary("invalid", details)
    if failures:
        status = "test_failed" if run.returncode == 1 else "invalid"
        return TestSummary(status, details)
    if run.returncode == 0 and any(
        case.find("skipped") is None for case in cases
    ):
        return TestSummary("passed")

    return TestSummary("invalid", ("No executed tests or inconsistent result",))
