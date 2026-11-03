import json
from pathlib import Path

import pytest

from reprofy.fixing import propose_verified_fix
from reprofy.models.base import Completion
from reprofy.models.fake import FakeBackend
from reprofy.patches import Edit, PatchProposal
from reprofy.reproduction import Reproduction
from reprofy.workspace import temporary_workspace
from tests.shop_fixture import BUGGY_SHOP


PROJECT = BUGGY_SHOP
SEARCH = "    discounted = subtotal * (1 - coupon_percent / 100)"
REPLACEMENT = (
    '    if coupon_percent > 100:\n'
    '        raise ValueError("Coupon percentage cannot exceed 100")\n'
    + SEARCH
)
REQUIREMENT = "Reject coupon_percent > 100 with ValueError; 100 remains valid."
REPRO = Reproduction(
    test_code=(
        "import pytest\nfrom shop.pricing import checkout_total\n\n"
        "def test_coupon():\n"
        "    assert checkout_total(100.0, coupon_percent=100) == 0\n"
        "    with pytest.raises(ValueError):\n"
        "        checkout_total(100.0, coupon_percent=101)\n"
    ),
    expected_failure="DID NOT RAISE ValueError",
)


def response(*, path="shop/pricing.py", search=SEARCH, replacement=REPLACEMENT):
    proposal = PatchProposal(explanation="Reject oversized coupons", edits=[
        Edit(path=path, search=search, replacement=replacement),
    ])
    return Completion(
        content=proposal.model_dump_json(), reasoning=None, finish_reason="stop",
        prompt_tokens=None, completion_tokens=None, reasoning_tokens=None,
        backend="fake", model="fixture", latency_seconds=0,
    )


def run(backend, *, project=PROJECT, reproduction=REPRO, max_attempts=3):
    return propose_verified_fix(
        backend, project, report="A coupon makes the total negative.",
        expected_behavior=REQUIREMENT, reproduction=reproduction,
        source_files=["shop/pricing.py"], context_files=["tests/test_shop.py"],
        max_attempts=max_attempts,
    )


def test_verified_patch_includes_regression_and_preserves_original():
    before = (PROJECT / "shop/pricing.py").read_bytes()
    backend = FakeBackend([response()])
    result = run(backend)
    assert result.status == "tests_verified", result.feedback
    assert result.attempts == 1
    assert "raise ValueError" in result.diff
    assert "--- /dev/null" in result.diff
    assert "tests/repro/test_reprofy_generated.py" in result.diff
    assert result.reproduction_run.returncode == result.suite_run.returncode == 0
    assert "10 passed" in result.suite_run.output
    assert (PROJECT / "shop/pricing.py").read_bytes() == before
    assert not (PROJECT / "tests/repro/test_reprofy_generated.py").exists()


def test_invalid_search_retries_with_feedback_from_original_snapshot():
    backend = FakeBackend([response(search="not present"), response()])
    result = run(backend)
    assert result.status == "tests_verified", result.feedback
    assert result.attempts == 2
    payload = json.loads(backend.calls[1]["messages"][1]["content"])
    assert "matched 0 times" in payload["previous_attempts"][0]
    assert REPLACEMENT not in payload["files"]["shop/pricing.py"]


def test_reproduction_passing_patch_cannot_hide_suite_regression():
    backend = FakeBackend([response(replacement=REPLACEMENT + "\n    return 0.0")])
    result = run(backend, max_attempts=1)
    assert result.status == "failed"
    assert "suite failed" in result.feedback[0]
    assert result.diff == ""


def test_attempt_budget_stops_without_touching_tests():
    backend = FakeBackend([response(path="tests/test_shop.py"), response(path="tests/test_shop.py")])
    result = run(backend, max_attempts=2)
    assert result.status == "failed"
    assert result.attempts == len(backend.calls) == 2
    assert all("allowed source path" in message for message in result.feedback)


@pytest.mark.parametrize("code", [
    "def test_pass():\n    assert True\n",
    "import nonexistent_reprofy_module\n",
    "import pytest\n@pytest.mark.skip\ndef test_skip():\n    pass\n",
])
def test_invalid_reproduction_does_not_spend_patch_calls(code):
    backend = FakeBackend([])
    result = run(backend, reproduction=Reproduction(test_code=code, expected_failure="fixture"))
    assert result.status == "invalid_reproduction"
    assert result.attempts == 0
    assert not backend.calls


def test_red_baseline_stops_before_patch_calls():
    with temporary_workspace(PROJECT) as project:
        (project / "tests/test_baseline_red.py").write_text("def test_red():\n    assert False\n")
        backend = FakeBackend([])
        result = run(backend, project=project)
    assert result.status == "baseline_failed"
    assert not backend.calls


def test_reproduction_cannot_rewrite_baseline_tests():
    code = (
        "from pathlib import Path\n"
        "def test_mutation():\n"
        "    Path('tests/test_shop.py').write_text('# removed')\n"
        "    assert False\n"
    )
    backend = FakeBackend([])
    result = run(backend, reproduction=Reproduction(test_code=code, expected_failure="fixture"))
    assert result.status == "invalid_reproduction"
    assert "modified protected files" in result.feedback[0]
    assert not backend.calls
