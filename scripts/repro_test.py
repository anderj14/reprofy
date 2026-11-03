import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from reprofy.context import gather_context
from reprofy.models.openai_compat import OpenAICompatBackend
from reprofy.models.usage import LoggedBackend
from reprofy.reproduction import generate_reproduction
from reprofy.test_runner import run_pytest, summarize_test_run
from reprofy.workspace import temporary_workspace


def main() -> None:
    ROOT = Path(__file__).resolve().parents[1]
    load_dotenv(ROOT / ".env")

    BACKENDS = {
        "meta": ("META_BASE_URL", "MODEL_API_KEY", "META_MODEL"),
        "baseten": ("BASETEN_BASE_URL", "BASETEN_API_KEY", "BASETEN_MODEL"),
    }

    name = sys.argv[1] if len(sys.argv) > 1 else "meta"
    url_var, key_var, model_var = BACKENDS[name]
    client = OpenAI(
        base_url=os.environ[url_var],
        api_key=os.environ[key_var],
        timeout=120.0,
        max_retries=2,
    )
    backend = LoggedBackend(
        OpenAICompatBackend(
            client, backend=name, model=os.environ[model_var],
        ),
        ROOT / ".reprofy/usage.jsonl",
    )

    project = ROOT / "examples/shop"
    context = gather_context(
        project, ["shop/pricing.py", "tests/test_shop.py"],
    )
    repro = generate_reproduction(
        backend,
        "El total queda negativo cuando uso un cupón.",
        context,
        expected_behavior=(
            "The maximum coupon percentage is 100. "
            "checkout_total must raise ValueError when coupon_percent > 100. "
            "Do not clamp the percentage or return zero for invalid coupons. "
            "A coupon of exactly 100 percent remains valid."
        ),
    )

    print("GENERATED TEST:\n", repro.test_code)
    print("EXPECTED FAILURE:", repro.expected_failure)

    with temporary_workspace(project) as workspace:
        baseline = run_pytest(workspace, ["tests"])
        if summarize_test_run(baseline).status != "passed":
            raise RuntimeError(f"Baseline failed:\n{baseline.output}")

        relative = "tests/repro/test_reprofy_generated.py"
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(relative)
        target.write_text(repro.test_code, encoding="utf-8")

        run = run_pytest(workspace, [relative])
        summary = summarize_test_run(run)

    print("OUTCOME:", summary.status)
    print(run.output)


if __name__ == "__main__":
    main()
