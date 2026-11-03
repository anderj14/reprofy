import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from reprofy.bugs import Bug, BugClassification, BugReport
from reprofy.cli import main
from reprofy.config import initialize_project
from reprofy.store import BugNotFound, StoreError, YamlBugStore, dump_bug


@pytest.fixture
def store(tmp_path):
    initialize_project(tmp_path)
    return YamlBugStore(tmp_path)


def test_reports_persist_with_optional_details_and_utc_timestamp(store):
    first = store.create(BugReport(text="  Se repite el último producto.  ", expected="Sin duplicados", actual="Hay uno repetido", context="Página 3"))
    second = store.create(BugReport(text="Correo duplicado"))
    assert [first.id, second.id] == ["BUG-001", "BUG-002"]
    reopened = YamlBugStore(store.project)
    assert reopened.get(first.id) == first
    assert first.status == "new" and first.revision == 0
    assert first.report.text == "Se repite el último producto."
    assert first.created_at.utcoffset() == timedelta(0)
    assert reopened.list() == [first, second]


def test_revisions_prevent_stale_updates(store):
    original = store.create(BugReport(text="Original report"))
    updated = store.save(original.model_copy(update={"report": BugReport(text="Updated report")}))
    assert updated.revision == 1
    assert store.get(original.id).report.text == "Updated report"
    with pytest.raises(StoreError, match="changed since"):
        store.save(original)
    assert store.get(original.id) == updated


def test_state_transitions_and_report_immutability_after_processing(store):
    original = store.create(BugReport(text="Original report"))
    with pytest.raises(StoreError, match="state transition"):
        store.save(original.model_copy(update={"status": "applied"}))
    classified = store.save(original.model_copy(update={
        "status": "classified",
        "classification": BugClassification(severity="medium", area="shop.catalog", suspected_files=["shop/catalog.py"], confidence=0.5),
    }))
    with pytest.raises(StoreError, match="Report cannot change"):
        store.save(classified.model_copy(update={"report": BugReport(text="Different report")}))
    failed = store.save(classified.model_copy(update={"status": "failed"}))
    with pytest.raises(StoreError, match="state transition"):
        store.save(failed.model_copy(update={"status": "new"}))


def test_creation_time_cannot_be_rewritten(store):
    original = store.create(BugReport(text="Report"))
    with pytest.raises(StoreError, match="creation time"):
        store.save(original.model_copy(update={"created_at": original.created_at + timedelta(seconds=1)}))


def test_numeric_ids_continue_past_999(store):
    bug = Bug(id="BUG-999", created_at=datetime.now(timezone.utc), report=BugReport(text="Existing report"))
    (store.project / ".reprofy/bugs/BUG-999.yaml").write_text(dump_bug(bug))
    assert store.create(BugReport(text="Next report")).id == "BUG-1000"
    assert [bug.id for bug in store.list()] == ["BUG-999", "BUG-1000"]


@pytest.mark.parametrize("value", ["../outside", "BUG-000", "BUG-0001", "BUG-1", "/tmp/report", "BUG-001.yaml"])
def test_invalid_ids_are_rejected(store, value):
    with pytest.raises(StoreError, match="Invalid bug ID"):
        store.get(value)


def test_unknown_id_is_explicit(store):
    with pytest.raises(BugNotFound, match="Bug not found"):
        store.get("BUG-001")


@pytest.mark.parametrize("text", [
    "id: BUG-001\nid: BUG-002\n",
    "report: [\n",
    "!!python/object/apply:os.system ['echo unexpected']\n",
    "report: {text: incomplete}\n",
])
def test_corrupt_records_are_not_silently_skipped_or_overwritten(store, text):
    record = store.project / ".reprofy/bugs/BUG-001.yaml"
    record.write_text(text)
    with pytest.raises(StoreError, match="Invalid bug record"):
        store.list()
    with pytest.raises(StoreError):
        store.create(BugReport(text="New report"))
    assert record.read_text() == text
    assert not (store.project / ".reprofy/bugs/.store.lock").exists()


def test_filename_id_mismatch_is_rejected(store):
    bug = store.create(BugReport(text="Report"))
    path = store.project / ".reprofy/bugs/BUG-001.yaml"
    path.write_text(dump_bug(bug.model_copy(update={"id": "BUG-002"})))
    with pytest.raises(StoreError, match="filename"):
        store.get("BUG-001")


def test_failed_atomic_update_preserves_existing_record(store, monkeypatch):
    original = store.create(BugReport(text="Original"))
    path = store.project / ".reprofy/bugs/BUG-001.yaml"
    before = path.read_bytes()
    with monkeypatch.context() as context:
        context.setattr("reprofy.store.os.replace", lambda *args: (_ for _ in ()).throw(OSError("simulated write failure")))
        with pytest.raises(OSError, match="simulated"):
            store.save(original.model_copy(update={"report": BugReport(text="Updated")}))
    assert path.read_bytes() == before
    assert store.get(original.id) == original
    assert not list(path.parent.glob("*.tmp"))
    assert not (path.parent / ".store.lock").exists()


def test_failed_new_record_publish_does_not_leave_partial_yaml(store, monkeypatch):
    with monkeypatch.context() as context:
        context.setattr("reprofy.store.os.link", lambda *args: (_ for _ in ()).throw(OSError("simulated publish failure")))
        with pytest.raises(OSError, match="simulated"):
            store.create(BugReport(text="Report"))
    assert not store.list()
    assert store.create(BugReport(text="Retry")).id == "BUG-001"


def test_existing_lock_is_not_removed_or_bypassed(store):
    lock = store.project / ".reprofy/bugs/.store.lock"
    lock.write_text("another writer")
    with pytest.raises(StoreError, match="locked"):
        store.create(BugReport(text="Report"))
    assert lock.read_text() == "another writer"
    assert not store.list()


def test_concurrent_creators_do_not_collide_or_corrupt_records(store):
    def create(index):
        try:
            return YamlBugStore(store.project).create(BugReport(text=f"Report {index}")).id
        except StoreError as error:
            assert "locked" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = [result for result in pool.map(create, range(8)) if result is not None]
    assert ids and len(ids) == len(set(ids))
    assert len(store.list()) == len(ids)


def test_symlink_record_is_rejected(store, tmp_path):
    outside = tmp_path / "outside.yaml"
    outside.write_text("private: synthetic\n")
    (store.project / ".reprofy/bugs/BUG-001.yaml").symlink_to(outside)
    with pytest.raises(StoreError, match="symlinks"):
        store.get("BUG-001")
    assert outside.read_text() == "private: synthetic\n"


@pytest.mark.parametrize("text", ["", " ", "\n\t"])
def test_blank_reports_are_rejected(text):
    with pytest.raises(ValidationError):
        BugReport(text=text)


def test_cli_report_list_show_and_status_filter_are_offline(store, capsys):
    project = str(store.project)
    with patch("openai.OpenAI", side_effect=AssertionError("Unexpected client creation")):
        assert main(["list", "--project", project, "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == []
        assert main(["report", "Producto repetido", "--expected", "Sin duplicados", "--project", project, "--json"]) == 0
        created = json.loads(capsys.readouterr().out)
        assert created["id"] == "BUG-001"
        assert created["report"]["expected"] == "Sin duplicados"
        assert main(["list", "--status", "new", "--project", project, "--json"]) == 0
        assert json.loads(capsys.readouterr().out)[0]["id"] == "BUG-001"
        assert main(["list", "--status", "failed", "--project", project, "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == []
        assert main(["show", "BUG-001", "--project", project, "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == created
        assert main(["show", "BUG-002", "--project", project]) == 1
        assert "Bug not found" in capsys.readouterr().err


def test_cli_persistence_survives_separate_processes(store):
    repository = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": str(repository / "src")}
    command = [sys.executable, "-m", "reprofy"]
    created = subprocess.run(
        [*command, "report", "Persisted across processes", "--project", str(store.project), "--json"],
        cwd=repository, env=environment, capture_output=True, text=True, check=True,
    )
    listed = subprocess.run(
        [*command, "list", "--project", str(store.project), "--json"],
        cwd=repository, env=environment, capture_output=True, text=True, check=True,
    )
    assert json.loads(listed.stdout)[0] == json.loads(created.stdout)


def test_cli_corruption_and_blank_report_fail_without_partial_records(store, capsys):
    assert main(["report", "  ", "--project", str(store.project)]) == 1
    assert not store.list()
    capsys.readouterr()
    path = store.project / ".reprofy/bugs/BUG-001.yaml"
    path.write_text("text: synthetic-private-value\n")
    assert main(["list", "--project", str(store.project)]) == 1
    output = capsys.readouterr()
    assert "Invalid bug record" in output.err
    assert "synthetic-private-value" not in output.err
