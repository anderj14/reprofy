"""Compare configured providers on identical clean shop fixtures.

Each case runs in a disposable project copy. The benchmark creates proposals
and captures metrics, but never applies them to the fixture or live shop.
"""

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

from reprofy.backends import build_step_backends
from reprofy.bugs import BugReport
from reprofy.config import initialize_project, load_config
from reprofy.engine import fix_bug
from reprofy.store import YamlBugStore


CASES = [
    {
        "name": "coupon",
        "report": "El total queda negativo cuando uso un cupón.",
        "expected": "Rechazar coupon_percent mayor que 100 con ValueError; 100 es válido.",
    },
    {
        "name": "pagination",
        "report": "La última página repite un producto.",
        "expected": "Cada producto debe aparecer exactamente una vez al recorrer todas las páginas.",
    },
    {
        "name": "email",
        "report": "Puedo registrar el mismo correo cambiando mayúsculas.",
        "expected": "El correo debe ser único sin distinguir mayúsculas y minúsculas.",
    },
]


def usage_totals(path):
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    steps = {}
    for record in records:
        step = record.get("step", "unknown")
        totals = steps.setdefault(step, {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "reasoning_tokens": 0, "latency_seconds": 0.0,
        })
        totals["calls"] += 1
        for field in ("prompt_tokens", "completion_tokens", "reasoning_tokens"):
            totals[field] += record.get(field) or 0
        totals["latency_seconds"] += record.get("latency_seconds") or 0.0
    return records, steps


def price_estimate(backend, model, prompt_tokens, completion_tokens):
    # Public list rates checked 2026-11-03; cached input discounts are excluded.
    known_model = (
        backend == "meta" and model.startswith("muse-spark-1.3")
    ) or (
        backend == "baseten" and "muse-spark-open" in model.lower()
    )
    if not known_model:
        return None
    return round((prompt_tokens * 1.25 + completion_tokens * 4.25) / 1_000_000, 8)


def evaluate(backend_name, env_file, output_root):
    repo = Path(__file__).resolve().parents[1]
    fixture = repo / "tests/fixtures/buggy_shop"
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = output_root / f"{backend_name}-{run_stamp}-{uuid4().hex[:8]}"
    output_dir.mkdir(parents=True, exist_ok=False)
    case_results = []

    for case in CASES:
        case_dir = output_dir / case["name"]
        case_dir.mkdir()
        with TemporaryDirectory(prefix=f"reprofy-eval-{case['name']}-") as temp:
            project = Path(temp) / "project"
            shutil.copytree(fixture, project)
            initialize_project(project, source_dirs=["shop"], tests_dir="tests", backend=backend_name)
            store = YamlBugStore(project)
            bug = store.create(BugReport(text=case["report"], expected=case["expected"]))
            run_id = uuid4().hex
            backends = build_step_backends(
                load_config(project),
                project, bug_id=bug.id, run_id=run_id, env_file=env_file,
            )
            started = monotonic()
            try:
                result = fix_bug(project, bug.id, backends, run_id=run_id)
            finally:
                backends.close()
            elapsed = monotonic() - started
            records, steps = usage_totals(project / ".reprofy/usage.jsonl") if (project / ".reprofy/usage.jsonl").exists() else ([], {})
            bug = store.get(bug.id)
            artifact_root = project / ".reprofy/proposals" / bug.id
            generated_reproductions = len(list(artifact_root.rglob("reproduction.json"))) if artifact_root.exists() else 0
            if artifact_root.exists():
                shutil.copytree(artifact_root, case_dir / "artifacts")
            prompt_tokens = sum(r.get("prompt_tokens") or 0 for r in records)
            completion_tokens = sum(r.get("completion_tokens") or 0 for r in records)
            model = backends.models.get("fix", {}).get("model", "unknown")
            case_results.append({
                "case": case["name"], "status": result.status,
                "models": backends.models,
                "fix_attempts": bug.fix.attempts if bug.fix else 0,
                "reproduction_attempts": bug.repro.attempts if bug.repro else generated_reproductions,
                "review_verdict": bug.repro.validation if bug.repro else None,
                "calls": len(records), "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "reasoning_tokens": sum(r.get("reasoning_tokens") or 0 for r in records),
                "provider_latency_seconds": round(sum(r.get("latency_seconds") or 0.0 for r in records), 3),
                "wall_seconds": round(elapsed, 3), "steps": steps,
                "estimated_list_cost_usd": price_estimate(backend_name, model, prompt_tokens, completion_tokens),
                "artifact_dir": f"{case_dir.name}/artifacts" if artifact_root.exists() else None,
            })
            print(
                f"CASE {case['name']}: {result.status}; {len(records)} model calls; "
                f"{generated_reproductions} reproduction attempt(s)", flush=True,
            )

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": backend_name,
        "cases": case_results,
        "summary": {
            "resolved": sum(row["status"] == "fix_verified" for row in case_results),
            "total": len(case_results),
            "calls": sum(row["calls"] for row in case_results),
            "prompt_tokens": sum(row["prompt_tokens"] for row in case_results),
            "completion_tokens": sum(row["completion_tokens"] for row in case_results),
            "provider_latency_seconds": round(sum(row["provider_latency_seconds"] for row in case_results), 3),
            "estimated_list_cost_usd": (
                round(sum(row["estimated_list_cost_usd"] for row in case_results), 8)
                if all(row["estimated_list_cost_usd"] is not None for row in case_results)
                else None
            ),
        },
        "pricing_note": "Raw list-rate estimate for Muse Spark 1.3 / Muse Spark Open at $1.25 per 1M input tokens and $4.25 per 1M output tokens, checked 2026-11-03; ignores cached input and account credits. Null means model rate was not recognized.",
        "safety_note": "Each case starts from tests/fixtures/buggy_shop in a disposable temp directory. Proposals are saved for review, never applied.",
    }
    manifest_path = output_dir / "results.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"SAVED: {manifest_path}")


def main():
    parser = argparse.ArgumentParser(description="Run the same three bug cases against one configured provider")
    parser.add_argument("backend", choices=["meta", "baseten"])
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path, default=Path("docs/evaluation"))
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error("Environment file not found; credentials are never printed or copied")
    evaluate(args.backend, args.env_file.resolve(), args.output_dir.resolve())


if __name__ == "__main__":
    main()
