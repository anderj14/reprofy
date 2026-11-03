from pathlib import Path

import pytest

from reprofy.patches import (
    Edit, PatchError, PatchProposal, prepare_patch, write_patch_to_workspace,
)


def proposal(*edits):
    return PatchProposal(explanation="Test repair", edits=list(edits))


@pytest.mark.parametrize("path", ["../outside.py", "/tmp/outside.py", "tests/test_calc.py", "src/../calc.py"])
def test_paths_outside_allowlist_are_rejected(tmp_path, path):
    with pytest.raises(PatchError, match="allowed source path"):
        prepare_patch(tmp_path, proposal(Edit(path=path, search="old", replacement="new")), ["calc.py"])


@pytest.mark.parametrize("content", ["missing", "old old"])
def test_search_requires_exactly_one_match_without_writing(tmp_path, content):
    path = tmp_path / "calc.py"
    path.write_text(content)
    with pytest.raises(PatchError, match="expected exactly one"):
        prepare_patch(tmp_path, proposal(Edit(path="calc.py", search="old", replacement="new")), ["calc.py"])
    assert path.read_text() == content


def test_all_edits_are_validated_before_any_write(tmp_path):
    (tmp_path / "calc.py").write_text("old")
    (tmp_path / "other.py").write_text("other")
    with pytest.raises(PatchError):
        prepare_patch(tmp_path, proposal(
            Edit(path="calc.py", search="old", replacement="new"),
            Edit(path="other.py", search="missing", replacement="new"),
        ), ["calc.py", "other.py"])
    assert (tmp_path / "calc.py").read_text() == "old"


def test_symlink_source_is_rejected(tmp_path):
    (tmp_path / "real.py").write_text("old")
    (tmp_path / "calc.py").symlink_to(tmp_path / "real.py")
    with pytest.raises(PatchError, match="Symlink"):
        prepare_patch(tmp_path, proposal(Edit(path="calc.py", search="old", replacement="new")), ["calc.py"])


def test_sequential_edits_and_missing_final_newline_diff(tmp_path):
    path = tmp_path / "calc.py"
    path.write_text("old")
    prepared = prepare_patch(tmp_path, proposal(
        Edit(path="calc.py", search="old", replacement="intermediate"),
        Edit(path="calc.py", search="intermediate", replacement="new"),
    ), ["calc.py"])
    assert path.read_text() == "old"
    assert "--- a/calc.py\n" in prepared.diff
    assert "\\ No newline at end of file\n" in prepared.diff
    write_patch_to_workspace(tmp_path, prepared)
    assert path.read_text() == "new"


def test_stale_source_rejects_all_writes(tmp_path):
    for name in ["calc.py", "other.py"]:
        (tmp_path / name).write_text("old")
    prepared = prepare_patch(tmp_path, proposal(*[
        Edit(path=name, search="old", replacement="new")
        for name in ["calc.py", "other.py"]
    ]), ["calc.py", "other.py"])
    (tmp_path / "other.py").write_text("changed")
    with pytest.raises(PatchError, match="Source changed"):
        write_patch_to_workspace(tmp_path, prepared)
    assert (tmp_path / "calc.py").read_text() == "old"


def test_crlf_is_preserved(tmp_path):
    path = tmp_path / "calc.py"
    path.write_bytes(b"old\r\nunchanged\r\n")
    prepared = prepare_patch(tmp_path, proposal(Edit(path="calc.py", search="old", replacement="new")), ["calc.py"])
    write_patch_to_workspace(tmp_path, prepared)
    assert path.read_bytes() == b"new\r\nunchanged\r\n"


def test_noop_is_rejected(tmp_path):
    (tmp_path / "calc.py").write_text("old")
    with pytest.raises(PatchError, match="does not change"):
        prepare_patch(tmp_path, proposal(Edit(path="calc.py", search="old", replacement="old")), ["calc.py"])
