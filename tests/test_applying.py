import shutil
import stat
from pathlib import Path
from unittest.mock import Mock

import pytest

from reprofy import applying
from reprofy.applying import (
    ApplyError, ArtifactChange, apply_proposal, create_apply_proposal,
    load_apply_proposal, prepare_application,
)
from reprofy.cli import main
from reprofy.models.fake import FakeBackend
from reprofy.patches import render_diff
from reprofy.test_runner import run_pytest
from reprofy.workspace import EXCLUDED, project_hashes, protected_files
from tests.test_fixing import PROJECT, REQUIREMENT, response, run


@pytest.fixture(scope="module")
def verified_result():
    result = run(FakeBackend([response()]))
    assert result.status == "tests_verified", result.feedback
    return result


@pytest.fixture
def candidate(tmp_path, verified_result):
    project = tmp_path / "shop"
    shutil.copytree(PROJECT, project, ignore=EXCLUDED)
    proposal = create_apply_proposal(
        project, verified_result, report="A coupon makes the total negative.",
        expected_behavior=REQUIREMENT,
    )
    return project, proposal


def test_approved_apply_writes_source_and_regression_together(candidate):
    project, proposal = candidate
    approval = Mock(return_value=True)
    result = apply_proposal(project, proposal, approve=approval)
    assert result.status == "applied"
    assert set(result.changed_files) == {change.path for change in proposal.changes}
    approval.assert_called_once_with(proposal.diff)
    for change in proposal.changes:
        assert (project / change.path).read_bytes() == change.after.encode()
    assert run_pytest(project, ["tests"]).returncode == 0
    assert not (project / ".reprofy/apply.lock").exists()


def test_rejection_changes_no_project_files(candidate):
    project, proposal = candidate
    before = protected_files(project)
    result = apply_proposal(project, proposal, approve=lambda diff: False)
    assert result.status == "rejected"
    assert protected_files(project) == before
    assert not (project / ".reprofy").exists()
    assert not (project / "tests/repro").exists()


@pytest.mark.parametrize("relative", ["shop/pricing.py", "tests/test_shop.py", "pyproject.toml", "new_module.py"])
def test_stale_source_tests_or_config_are_rejected_before_approval(candidate, relative):
    project, proposal = candidate
    with (project / relative).open("a") as stream:
        stream.write("\n# edited after verification\n")
    approval = Mock(return_value=True)
    with pytest.raises(ApplyError, match="changed"):
        apply_proposal(project, proposal, approve=approval)
    approval.assert_not_called()
    assert not (project / "tests/repro").exists()


def test_edit_during_approval_is_preserved_and_aborts_apply(candidate):
    project, proposal = candidate
    path = project / "shop/pricing.py"
    edited = path.read_bytes() + b"\n# concurrent developer edit\n"

    def approve(diff):
        path.write_bytes(edited)
        return True

    with pytest.raises(ApplyError, match="changed"):
        apply_proposal(project, proposal, approve=approve)
    assert path.read_bytes() == edited
    assert not (project / "tests/repro").exists()


def test_mutating_callers_artifact_cannot_swap_reviewed_changes(candidate):
    project, proposal = candidate
    expected = proposal.changes[0].after

    def approve(diff):
        original = proposal.changes[0]
        proposal.changes[0] = original.model_copy(update={"after": "raise RuntimeError('swapped')\n"})
        return True

    assert apply_proposal(project, proposal, approve=approve).status == "applied"
    assert (project / "shop/pricing.py").read_text() == expected


def test_tampered_diff_is_rejected(candidate):
    project, proposal = candidate
    with pytest.raises(ApplyError, match="diff does not match"):
        prepare_application(project, proposal.model_copy(update={"diff": "fake diff"}))


def test_existing_regression_file_is_never_overwritten(candidate):
    project, proposal = candidate
    target = project / proposal.reproduction_path
    target.parent.mkdir()
    target.write_text("# user file")
    with pytest.raises(ApplyError):
        apply_proposal(project, proposal, approve=lambda diff: True)
    assert target.read_text() == "# user file"


def test_symlink_regression_directory_is_rejected(candidate, tmp_path):
    project, proposal = candidate
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "tests/repro").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ApplyError, match="Symlink"):
        prepare_application(project, proposal)
    assert not list(outside.iterdir())


def test_existing_tests_cannot_be_added_to_source_allowlist(candidate):
    project, proposal = candidate
    test = "tests/test_shop.py"
    changed = ArtifactChange(path=test, before=(project / test).read_text(), after="# removed tests\n")
    tampered = proposal.model_copy(update={
        "changes": [changed, proposal.changes[-1]], "source_files": [test],
    })
    with pytest.raises(ApplyError, match="not editable source"):
        prepare_application(project, tampered)


def test_wrong_project_is_rejected(candidate, tmp_path):
    _, proposal = candidate
    with pytest.raises(ApplyError, match="different project"):
        prepare_application(tmp_path, proposal)


def test_saved_patch_must_pass_fresh_tests_before_approval(candidate):
    project, proposal = candidate
    source = proposal.changes[0]
    bad = source.model_copy(update={"after": source.before + "\n# no actual fix\n"})
    changes = [bad, proposal.changes[-1]]
    diff = "".join(render_diff(c.path, c.before or "", c.after, new_file=c.before is None) for c in changes)
    tampered = proposal.model_copy(update={"changes": changes, "diff": diff})
    approval = Mock(return_value=True)
    before = protected_files(project)
    with pytest.raises(ApplyError, match="no longer verifies"):
        apply_proposal(project, tampered, approve=approval)
    approval.assert_not_called()
    assert protected_files(project) == before


def test_second_file_write_failure_rolls_back_first_file(candidate, monkeypatch):
    project, proposal = candidate
    before = protected_files(project)
    real_replace = applying._replace
    calls = 0

    def fail_once(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk failure")
        real_replace(source, target)

    monkeypatch.setattr(applying, "_replace", fail_once)
    with pytest.raises(ApplyError, match="restored"):
        apply_proposal(project, proposal, approve=lambda diff: True)
    assert protected_files(project) == before
    assert not (project / "tests/repro").exists()
    assert not (project / ".reprofy/apply.lock").exists()


def test_failed_recovery_keeps_backups_and_reports_location(candidate, monkeypatch):
    project, proposal = candidate
    real_replace = applying._replace
    calls = 0

    def fail_after_first(source, target):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("simulated unavailable disk")
        real_replace(source, target)

    monkeypatch.setattr(applying, "_replace", fail_after_first)
    with pytest.raises(ApplyError, match="recovery files kept"):
        apply_proposal(project, proposal, approve=lambda diff: True)
    assert list((project / ".reprofy").glob("apply-*/*.backup"))


def test_existing_apply_lock_blocks_writes(candidate):
    project, proposal = candidate
    (project / ".reprofy").mkdir()
    lock = project / ".reprofy/apply.lock"
    lock.write_text("another apply")
    before = protected_files(project)
    with pytest.raises(ApplyError, match="lock exists"):
        apply_proposal(project, proposal, approve=lambda diff: True)
    assert protected_files(project) == before
    assert lock.read_text() == "another apply"


def test_rollback_does_not_overwrite_concurrent_developer_edit(candidate, monkeypatch):
    project, proposal = candidate
    real_replace = applying._replace
    target = project / proposal.changes[0].path
    developer_content = proposal.changes[0].after + "\n# concurrent edit\n"
    calls = 0

    def fail_after_developer_edit(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            target.write_text(developer_content)
            raise OSError("simulated failure after concurrent edit")
        real_replace(source, destination)

    monkeypatch.setattr(applying, "_replace", fail_after_developer_edit)
    with pytest.raises(ApplyError, match="recovery files kept"):
        apply_proposal(project, proposal, approve=lambda diff: True)
    assert target.read_text() == developer_content
    assert list((project / ".reprofy").glob("apply-*/recovery.json"))


def test_metadata_symlink_cannot_redirect_transaction(candidate, tmp_path):
    project, proposal = candidate
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".reprofy").symlink_to(outside, target_is_directory=True)
    before = protected_files(project)
    with pytest.raises(ApplyError, match="must not be a symlink"):
        apply_proposal(project, proposal, approve=lambda diff: True)
    assert protected_files(project) == before
    assert not list(outside.iterdir())


def test_source_mode_is_preserved(candidate):
    project, proposal = candidate
    target = project / "shop/pricing.py"
    target.chmod(0o744)
    apply_proposal(project, proposal, approve=lambda diff: True)
    assert stat.S_IMODE(target.stat().st_mode) == 0o744


def test_repeat_apply_is_refused(candidate):
    project, proposal = candidate
    apply_proposal(project, proposal, approve=lambda diff: True)
    with pytest.raises(ApplyError, match="changed"):
        apply_proposal(project, proposal, approve=lambda diff: True)


def test_cli_check_and_noninteractive_approval(candidate, tmp_path, monkeypatch, capsys):
    project, proposal = candidate
    artifact = tmp_path / "apply.json"
    artifact.write_text(proposal.model_dump_json())
    before = protected_files(project)
    assert load_apply_proposal(artifact) == proposal
    assert main(["apply", str(artifact), "--project", str(project), "--check"]) == 0
    assert "READY" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["apply", str(artifact), "--project", str(project)]) == 1
    assert "Interactive approval is required" in capsys.readouterr().err
    assert protected_files(project) == before


@pytest.mark.parametrize("answer, expected", [("yes", "REJECTED"), ("APPLY", "APPLIED")])
def test_cli_requires_exact_explicit_confirmation(candidate, tmp_path, monkeypatch, capsys, answer, expected):
    project, proposal = candidate
    artifact = tmp_path / "apply.json"
    artifact.write_text(proposal.model_dump_json())
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    assert main(["apply", str(artifact), "--project", str(project)]) == 0
    assert expected in capsys.readouterr().out
    assert (project / proposal.reproduction_path).exists() == (answer == "APPLY")
