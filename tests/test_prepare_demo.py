import pytest

from scripts.prepare_demo import prepare_demo
from reprofy.store import YamlBugStore


def test_prepare_demo_creates_clean_configured_bug_fixture_without_api_calls(tmp_path):
    project, bug_id = prepare_demo(tmp_path / "demo")
    assert bug_id == "BUG-001"
    bug = YamlBugStore(project).get(bug_id)
    assert bug.status == "new"
    assert "repite" in bug.report.text
    assert (project / "reprofy.yaml").is_file()
    assert "Seeded defect" in (project / "shop/catalog.py").read_text()
    assert not (project / ".reprofy/usage.jsonl").exists()


def test_prepare_demo_refuses_to_overwrite(tmp_path):
    project = tmp_path / "existing"
    project.mkdir()
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        prepare_demo(project)
