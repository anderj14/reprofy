import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from reprofy.applying import apply_proposal, load_apply_proposal, prepare_application
from reprofy.backends import StepBackends
from reprofy.bugs import BugReport
from reprofy.cli import main
from reprofy.config import dump_config, initialize_project
from reprofy.engine import EngineError, fix_bug
from reprofy.models.base import Completion
from reprofy.models.fake import FakeBackend
from reprofy.store import YamlBugStore
from reprofy.workspace import protected_files


REPRO = (
    "from inventory.stock import available\n\n"
    "def test_reserved_stock_is_subtracted():\n"
    "    assert available(5, 2) == 3\n"
)


def completion(payload):
    return Completion(
        content=json.dumps(payload), reasoning=None, finish_reason="stop",
        prompt_tokens=None, completion_tokens=None, reasoning_tokens=None,
        backend="fake", model="fixture", latency_seconds=0,
    )


def backends(*, reproduction=REPRO, search="return stock + reserved", attempts=1):
    return StepBackends(
        classify=FakeBackend([completion({
            "severity": "medium", "area": "inventory.stock",
            "suspected_files": ["inventory/stock.py"], "confidence": 0.6,
        })]),
        repro=FakeBackend([completion({"test_code": reproduction, "expected_failure": "Expected 3 but got 7"})]),
        fix=FakeBackend([completion({
            "explanation": "Subtract reservations from stock",
            "edits": [{"path": "inventory/stock.py", "search": search, "replacement": "return stock - reserved"}],
        }) for _ in range(attempts)]),
    )


@pytest.fixture
def project(tmp_path):
    (tmp_path / "inventory").mkdir()
    (tmp_path / "inventory/__init__.py").write_text("")
    (tmp_path / "inventory/stock.py").write_text("def available(stock, reserved):\n    return stock + reserved\n")
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks/test_stock.py").write_text(
        "from inventory.stock import available\n\n"
        "def test_no_reservations():\n    assert available(5, 0) == 5\n"
    )
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\ntestpaths = ["checks"]\n')
    config = initialize_project(tmp_path, source_dirs=["inventory"], tests_dir="checks")
    config.max_attempts = 2
    config.test_timeout_seconds = 15
    config.test_command.append("-x")
    config.models.classify.max_completion_tokens = 1024
    config.models.repro.max_completion_tokens = 4096
    config.models.fix.reasoning_effort = "medium"
    config.models.fix.max_completion_tokens = 4096
    (tmp_path / "reprofy.yaml").write_text(dump_config(config))
    store = YamlBugStore(tmp_path)
    store.create(BugReport(text="Stock increases when reserving an item", expected="Available stock must subtract reserved units"))
    return tmp_path


def test_generic_workflow_uses_config_and_persists_reviewable_result(project):
    before = protected_files(project)
    steps = backends()
    events = []
    result = fix_bug(project, "BUG-001", steps, on_event=events.append)
    assert result.status == "fix_verified", result.error
    assert protected_files(project) == before
    bug = YamlBugStore(project).get("BUG-001")
    assert bug.status == "fix_verified" and bug.fix.tests_pass
    assert bug.fix.attempts == 1
    assert bug.repro.test_file == "tests/repro/test_bug_001.py"
    assert bug.repro.fails_as_expected is False  # No automatic semantic acceptance claim.
    proposal = load_apply_proposal(Path(result.apply_file))
    assert proposal.bug_id == bug.id and proposal.bug_revision == bug.revision
    assert proposal.suite_targets == ["checks"]
    assert proposal.test_command == ["{python}", "-m", "pytest", "-x"]
    assert proposal.timeout_seconds == 15
    assert "tests/repro/test_bug_001.py" in result.diff
    assert steps.classify.calls[0]["max_completion_tokens"] == 1024
    assert steps.repro.calls[0]["max_completion_tokens"] == 4096
    assert steps.fix.calls[0]["reasoning_effort"] == "medium"
    assert steps.fix.calls[0]["max_completion_tokens"] == 4096
    assert any("classified" in event for event in events)
    prepare_application(project, proposal)
    applied = apply_proposal(project, proposal, approve=lambda diff: True)
    assert applied.status == "applied" and applied.bookkeeping_warning is None
    updated = YamlBugStore(project).get("BUG-001")
    assert updated.status == "applied" and updated.repro.fails_as_expected


def test_rejection_is_persisted_without_source_changes(project):
    before = protected_files(project)
    result = fix_bug(project, "BUG-001", backends())
    proposal = load_apply_proposal(Path(result.apply_file))
    assert apply_proposal(project, proposal, approve=lambda diff: False).status == "rejected"
    assert YamlBugStore(project).get("BUG-001").status == "rejected"
    assert protected_files(project) == before


def test_baseline_failure_spends_no_calls_and_can_be_retried(project):
    test = project / "checks/test_stock.py"
    original = test.read_text()
    test.write_text(original.replace("== 5", "== 42"))
    steps = backends()
    failed = fix_bug(project, "BUG-001", steps)
    assert failed.status == "baseline_failed"
    assert YamlBugStore(project).get("BUG-001").status == "new"
    assert not steps.classify.calls and not steps.repro.calls and not steps.fix.calls
    test.write_text(original)
    assert fix_bug(project, "BUG-001", steps).status == "fix_verified"


def test_nonfailing_reproduction_is_not_sent_to_patch_model(project):
    steps = backends(reproduction="def test_no_bug():\n    assert True\n")
    result = fix_bug(project, "BUG-001", steps)
    assert result.status == "invalid_reproduction"
    assert YamlBugStore(project).get("BUG-001").status == "failed"
    assert not steps.fix.calls and result.apply_file is None


def test_exhausted_patch_budget_is_persisted(project):
    steps = backends(search="not present", attempts=2)
    result = fix_bug(project, "BUG-001", steps)
    bug = YamlBugStore(project).get("BUG-001")
    assert result.status == "failed" and bug.status == "failed"
    assert bug.fix.attempts == 2 and len(steps.fix.calls) == 2
    assert not bug.fix.tests_pass and result.apply_file is None


def test_external_bug_update_invalidates_saved_proposal(project):
    result = fix_bug(project, "BUG-001", backends())
    store = YamlBugStore(project)
    bug = store.get("BUG-001")
    store.save(bug)
    with pytest.raises(ValueError, match="revision changed"):
        prepare_application(project, load_apply_proposal(Path(result.apply_file)))


def test_external_source_edit_during_generation_is_preserved(project):
    steps = backends()
    complete = steps.fix.complete

    def with_edit(*args, **kwargs):
        path = project / "inventory/stock.py"
        path.write_text(path.read_text() + "\n# user edit\n")
        return complete(*args, **kwargs)

    steps.fix.complete = with_edit
    result = fix_bug(project, "BUG-001", steps)
    assert result.status == "failed" and result.apply_file is None
    assert "# user edit" in (project / "inventory/stock.py").read_text()


def test_bookkeeping_failure_does_not_misreport_successful_source_application(project, monkeypatch):
    result = fix_bug(project, "BUG-001", backends())
    proposal = load_apply_proposal(Path(result.apply_file))
    monkeypatch.setattr("reprofy.applying.YamlBugStore.save", Mock(side_effect=OSError("simulated metadata failure")))
    applied = apply_proposal(project, proposal, approve=lambda diff: True)
    assert applied.status == "applied"
    assert "bug status could not be saved" in applied.bookkeeping_warning
    assert "return stock - reserved" in (project / "inventory/stock.py").read_text()


def test_cli_fix_json_and_bug_id_apply_check(project, monkeypatch, capsys):
    steps = backends()
    monkeypatch.setattr("reprofy.cli.build_step_backends", lambda *args, **kwargs: steps)
    assert main(["fix", "BUG-001", "--project", str(project), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "fix_verified"
    before_revision = YamlBugStore(project).get("BUG-001").revision
    assert main(["apply", "BUG-001", "--project", str(project), "--check"]) == 0
    assert "READY" in capsys.readouterr().out
    assert YamlBugStore(project).get("BUG-001").revision == before_revision


def test_missing_expected_behavior_stops_before_provider_configuration(project, monkeypatch, capsys):
    store = YamlBugStore(project)
    store.create(BugReport(text="Unclear report"))
    constructor = Mock(side_effect=AssertionError("Unexpected provider setup"))
    monkeypatch.setattr("reprofy.cli.build_step_backends", constructor)
    assert main(["fix", "BUG-002", "--project", str(project)]) == 1
    assert "Expected behavior is required" in capsys.readouterr().err
    constructor.assert_not_called()
    assert store.get("BUG-002").status == "new"
