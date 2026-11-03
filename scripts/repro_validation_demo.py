"""Offline demonstration of reproduction retries and semantic review."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from reprofy.backends import StepBackends
from reprofy.bugs import BugReport
from reprofy.config import dump_config, initialize_project
from reprofy.engine import fix_bug
from reprofy.models.base import Completion
from reprofy.models.fake import FakeBackend
from reprofy.store import YamlBugStore


VALID = (
    "from inventory.stock import available\n\n"
    "def test_reserved_stock_is_subtracted():\n"
    "    assert available(5, 2) == 3\n"
)


def completion(payload):
    return Completion(
        content=json.dumps(payload), reasoning=None, finish_reason="stop",
        prompt_tokens=None, completion_tokens=None, reasoning_tokens=None,
        backend="fake", model="offline-demo", latency_seconds=0,
    )


def run(scenario):
    with TemporaryDirectory(prefix="reprofy-demo-") as directory:
        project = Path(directory)
        (project / "inventory").mkdir()
        (project / "inventory/__init__.py").write_text("")
        (project / "inventory/stock.py").write_text("def available(stock, reserved):\n    return stock + reserved\n")
        (project / "checks").mkdir()
        (project / "checks/test_stock.py").write_text(
            "from inventory.stock import available\n"
            "def test_no_reservations():\n    assert available(5, 0) == 5\n"
        )
        (project / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\npythonpath = ["."]\ntestpaths = ["checks"]\n'
        )
        config = initialize_project(project, source_dirs=["inventory"], tests_dir="checks")
        if scenario == "timeout":
            config.test_timeout_seconds = 1
            (project / "reprofy.yaml").write_text(dump_config(config), encoding="utf-8")
        YamlBugStore(project).create(BugReport(
            text="Available stock increases when units are reserved",
            expected="Available stock must equal stock minus reserved units",
        ))
        invalid = "def test_broken():\n    import missing_demo_module\n    assert True\n"
        unrelated = (
            "from inventory.stock import available\n"
            "def test_unrelated_case():\n    assert available(5, 2) == 99\n"
        )
        if scenario == "repair":
            repros = [invalid, VALID]
            reviews = [{"verdict": "accept", "reason": "The assertion tests reserved stock.", "observed_cause": "Reservation is added instead of subtracted."}]
        elif scenario == "semantic-reject":
            repros = [unrelated, VALID]
            reviews = [
                {"verdict": "reject", "reason": "The test asserts an unrelated result.", "observed_cause": "The chosen expected number has no link to the report."},
                {"verdict": "accept", "reason": "The assertion matches the stated requirement.", "observed_cause": "Reservation is added instead of subtracted."},
            ]
        elif scenario == "timeout":
            repros = ["def test_hangs():\n    while True: pass\n", VALID]
            reviews = [{"verdict": "accept", "reason": "The assertion tests reserved stock.", "observed_cause": "Reservation is added instead of subtracted."}]
        else:
            repros = [invalid] * 3
            reviews = []
        steps = StepBackends(
            classify=FakeBackend([completion({
                "severity": "medium", "area": "inventory.stock",
                "suspected_files": ["inventory/stock.py"], "confidence": 0.8,
            })]),
            repro=FakeBackend([completion({"test_code": code, "expected_failure": "Available stock is 7 instead of 3."}) for code in repros]),
            review=FakeBackend([completion(review) for review in reviews]),
            fix=FakeBackend([completion({
                "explanation": "Subtract reservations from available stock.",
                "edits": [{"path": "inventory/stock.py", "search": "return stock + reserved", "replacement": "return stock - reserved"}],
            })]),
        )
        result = fix_bug(project, "BUG-001", steps)
        bug = YamlBugStore(project).get("BUG-001")
        print(f"scenario: {scenario}")
        print(f"result: {result.status}; generated attempts: {len(steps.repro.calls)}; semantic reviews: {len(steps.review.calls)}")
        print(f"bug state: {bug.status}; source untouched: {'return stock + reserved' in (project / 'inventory/stock.py').read_text()}")
        print("note: the disposable demo project is removed when this command exits")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["repair", "semantic-reject", "timeout", "exhaust"])
    run(parser.parse_args().scenario)


if __name__ == "__main__":
    main()
