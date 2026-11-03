import json

from scripts.evaluate_backends import price_estimate, usage_totals


def test_usage_totals_aggregate_calls_by_step(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in [
        {"step": "classify", "prompt_tokens": 100, "completion_tokens": 20, "reasoning_tokens": 5, "latency_seconds": 1.2},
        {"step": "repro", "prompt_tokens": 200, "completion_tokens": 40, "reasoning_tokens": 15, "latency_seconds": 2.3},
        {"step": "repro_review", "prompt_tokens": 300, "completion_tokens": 30, "reasoning_tokens": 10, "latency_seconds": 0.5},
        {"step": "fix", "prompt_tokens": 400, "completion_tokens": 60, "reasoning_tokens": 25, "latency_seconds": 3.0},
    ]), encoding="utf-8")
    records, steps = usage_totals(path)
    assert len(records) == 4
    assert steps["repro_review"]["prompt_tokens"] == 300
    assert steps["fix"]["calls"] == 1
    assert steps["classify"]["latency_seconds"] == 1.2


def test_price_estimate_applies_published_rates_only_to_known_models():
    assert price_estimate("meta", "muse-spark-1.3", 1_000_000, 1_000_000) == 5.5
    assert price_estimate("baseten", "meta-models/muse-spark-open-1026", 1_000_000, 1_000_000) == 5.5
    assert price_estimate("meta", "unexpected-model", 1_000_000, 1_000_000) is None
