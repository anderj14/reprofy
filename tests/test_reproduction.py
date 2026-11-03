import json
from pathlib import Path

from reprofy.models.base import Completion
from reprofy.models.fake import FakeBackend
from reprofy.reproduction import Reproduction, generate_reproduction
from reprofy.test_runner import run_pytest, summarize_test_run
from reprofy.workspace import temporary_workspace


def test_explicit_requirement_reaches_generation_and_reproduces_missing_validation():
    requirement = "Coupon percentages above 100 must raise ValueError; exactly 100 is valid."
    prepared = Reproduction(
        test_code=(
            "import pytest\n"
            "from shop.pricing import checkout_total\n\n"
            "def test_oversized_coupon_is_rejected():\n"
            "    with pytest.raises(ValueError):\n"
            "        checkout_total(100.0, coupon_percent=150)\n"
        ),
        expected_failure="DID NOT RAISE ValueError because validation is missing.",
    )
    backend = FakeBackend([Completion(
        content=prepared.model_dump_json(), reasoning=None, finish_reason="stop",
        prompt_tokens=None, completion_tokens=None, reasoning_tokens=None,
        backend="fake", model="fixture", latency_seconds=0.0,
    )])
    reproduction = generate_reproduction(
        backend, "A coupon makes checkout negative.",
        {"shop/pricing.py": "# Synthetic context for the fake backend"},
        expected_behavior=requirement,
    )
    sent = json.loads(backend.calls[0]["messages"][1]["content"])
    assert sent["expected_behavior"] == requirement

    project = Path(__file__).resolve().parents[1] / "examples/shop"
    original = project / "shop/pricing.py"
    before = original.read_bytes()
    with temporary_workspace(project) as workspace:
        target = workspace / "tests/repro/test_coupon.py"
        target.parent.mkdir(parents=True)
        target.write_text(reproduction.test_code, encoding="utf-8")
        run = run_pytest(workspace, ["tests/repro/test_coupon.py"])

    assert summarize_test_run(run).status == "test_failed", run.output
    assert "DID NOT RAISE" in run.output
    assert original.read_bytes() == before
