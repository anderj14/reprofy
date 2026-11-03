"""Development probe: report to test-verified diff. Never applies to the original shop."""

import argparse
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from openai import OpenAI

from reprofy.classification import classify_report
from reprofy.applying import create_apply_proposal
from reprofy.context import gather_context
from reprofy.fixing import propose_verified_fix
from reprofy.models.openai_compat import OpenAICompatBackend
from reprofy.models.usage import LoggedBackend
from reprofy.repo_map import build_repo_map
from reprofy.reproduction import generate_reproduction
from reprofy.test_runner import run_pytest, summarize_test_run
from reprofy.workspace import temporary_workspace


REPORT = "El total queda negativo cuando uso un cupón."
EXPECTED = (
    "The maximum coupon percentage is 100. checkout_total must raise ValueError "
    "when coupon_percent > 100. Do not clamp the percentage or return zero for "
    "invalid coupons. A coupon of exactly 100 percent remains valid."
)
BACKENDS = {
    "meta": ("META_BASE_URL", "MODEL_API_KEY", "META_MODEL"),
    "baseten": ("BASETEN_BASE_URL", "BASETEN_API_KEY", "BASETEN_MODEL"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", choices=BACKENDS, nargs="?", default="meta")
    parser.add_argument("--max-attempts", type=int, choices=range(1, 6), default=3)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    url_var, key_var, model_var = BACKENDS[args.backend]
    model = os.environ[model_var]
    client = OpenAI(
        base_url=os.environ[url_var], api_key=os.environ[key_var],
        timeout=120.0, max_retries=2,
    )
    backend = LoggedBackend(
        OpenAICompatBackend(client, backend=args.backend, model=model),
        root / ".reprofy/usage.jsonl",
    )
    print(f"BACKEND: {args.backend} / {model}", flush=True)

    # Take the snapshot and check baseline before any paid model call.
    with temporary_workspace(root / "examples/shop") as snapshot:
        baseline = run_pytest(snapshot, ["tests"])
        if summarize_test_run(baseline).status != "passed":
            raise RuntimeError(f"Baseline failed:\n{baseline.output}")
        modules = build_repo_map(snapshot, ["shop"])
        classification = classify_report(backend, REPORT, modules)
        classified = classification.model_dump(mode="json")
        print("CLASSIFICATION:", json.dumps(classified), flush=True)
        context = gather_context(snapshot, [*classified["suspected_files"], "tests/test_shop.py"])
        reproduction = generate_reproduction(
            backend, REPORT, context, expected_behavior=EXPECTED,
        )
        artifact_dir = root / ".reprofy/proposals" / uuid4().hex
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "reproduction.py").write_text(reproduction.test_code, encoding="utf-8")
        print("GENERATED TEST:\n" + reproduction.test_code, flush=True)
        print("EXPECTED FAILURE:", reproduction.expected_failure, flush=True)

        source_files = sorted(modules.values())
        original_hashes = {
            relative: hashlib.sha256((snapshot / relative).read_bytes()).hexdigest()
            for relative in source_files
        }
        result = propose_verified_fix(
            backend, snapshot, report=REPORT, expected_behavior=EXPECTED,
            reproduction=reproduction, source_files=source_files,
            context_files=["tests/test_shop.py"], max_attempts=args.max_attempts,
        )

    record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "backend": args.backend, "requested_model": model,
        "report": REPORT, "expected_behavior": EXPECTED,
        "classification": classified,
        "reproduction": reproduction.model_dump(),
        "original_source_sha256": original_hashes,
        "semantic_reproduction_acceptance": "pending independent review",
        "applied": False,
        "result": asdict(result),
    }
    (artifact_dir / "result.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    if result.status == "tests_verified":
        apply_proposal = create_apply_proposal(
            root / "examples/shop", result, report=REPORT, expected_behavior=EXPECTED,
        )
        (artifact_dir / "apply.json").write_text(apply_proposal.model_dump_json(indent=2), encoding="utf-8")
        (artifact_dir / "proposal.patch").write_text(result.diff, encoding="utf-8")
        print("PROPOSED DIFF:\n" + result.diff)
        print("REPRODUCTION:", result.reproduction_run.output)
        print("FULL SUITE:", result.suite_run.output)
    else:
        print("FEEDBACK:", json.dumps(result.feedback, indent=2))
    print(f"OUTCOME: {result.status}; patch attempts: {result.attempts}")
    print("ARTIFACTS:", artifact_dir)
    print("Original project source was not patched. Review is required before any apply.")
    if result.status != "tests_verified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
