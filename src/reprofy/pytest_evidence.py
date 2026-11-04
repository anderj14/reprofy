"""Explicit pytest plugin recording execution phases and project-call evidence.

Imported by pytest subprocesses only. This observes execution; it is not a sandbox.
"""

import json
import sys
import threading
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--reprofy-evidence-file", default=None)
    parser.addoption("--reprofy-source-files", default="[]")


def pytest_configure(config):
    destination = config.getoption("--reprofy-evidence-file")
    if destination:
        config.pluginmanager.register(Recorder(
            Path(destination), json.loads(config.getoption("--reprofy-source-files")),
        ), "reprofy-execution-recorder")


class Recorder:
    def __init__(self, destination, source_files):
        self.destination = destination
        self.root = Path.cwd().resolve()
        self.allowed = {str((self.root / name).resolve()) for name in source_files}
        self.calls = {}
        self.cache = {}
        self.reports = []
        self.collection_errors = []

    def relative(self, filename):
        path = Path(str(filename)).resolve()
        if path.is_relative_to(self.root):
            return path.relative_to(self.root).as_posix()
        return f"<external>/{path.name}"

    def trace_phase(self, item):
        called = self.calls.setdefault(item.nodeid, set())
        previous = sys.getprofile()
        previous_thread = threading.getprofile()

        def make_profile(previous_profile):
            def profile(frame, event, arg):
                if event == "call":
                    filename = frame.f_code.co_filename
                    if filename not in self.cache:
                        try:
                            resolved = str(Path(filename).resolve())
                            self.cache[filename] = self.relative(resolved) if resolved in self.allowed else None
                        except OSError:
                            self.cache[filename] = None
                    relative = self.cache[filename]
                    if relative:
                        called.add(relative)
                if previous_profile:
                    previous_profile(frame, event, arg)
            return profile

        profile = make_profile(previous)
        thread_profile = make_profile(previous_thread)

        if self.allowed:
            sys.setprofile(profile)
            threading.setprofile(thread_profile)
        try:
            yield
        finally:
            if self.allowed:
                threading.setprofile(previous_thread)
                sys.setprofile(previous)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_setup(self, item):
        yield from self.trace_phase(item)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        yield from self.trace_phase(item)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        record = {
            "nodeid": item.nodeid, "phase": report.when, "outcome": report.outcome,
            "called_files": sorted(self.calls.get(item.nodeid, set())),
            "exception_type": None, "message": None, "frames": [],
        }
        if call.excinfo is not None:
            exception = call.excinfo.type
            record["exception_type"] = f"{exception.__module__}.{exception.__name__}"
            try:
                record["message"] = str(call.excinfo.value)[:4000]
            except Exception:
                record["message"] = "<exception message unavailable>"
            record["frames"] = [
                {"path": self.relative(entry.path), "line": entry.lineno + 1, "function": entry.name}
                for entry in list(call.excinfo.traceback)[-25:]
            ]
        self.reports.append(record)

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append({"nodeid": report.nodeid, "details": str(report.longrepr)[:4000]})

    def pytest_sessionfinish(self, session, exitstatus):
        self.destination.write_text(json.dumps({
            "version": 1, "exitstatus": int(exitstatus),
            "reports": self.reports, "collection_errors": self.collection_errors,
        }, ensure_ascii=False), encoding="utf-8")
