import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

from pathlib import Path
from reprofy.classification import classify_report
from reprofy.models.openai_compat import OpenAICompatBackend
from reprofy.models.usage import LoggedBackend
from reprofy.repo_map import build_repo_map

load_dotenv()

BACKENDS = {
    "baseten": ("BASETEN_BASE_URL", "BASETEN_API_KEY", "BASETEN_MODEL"),
    "meta": ("META_BASE_URL", "MODEL_API_KEY", "META_MODEL"),
}

ROOT = Path(__file__).resolve().parents[1]
REPORT = "The total becomes negative when a coupon is used."

def run(name: str) -> None:
    if name not in BACKENDS:
        raise SystemExit("Choose a backend: meta or baseten")

    url_var, key_var, model_var = BACKENDS[name]
    model = os.environ[model_var]

    client = OpenAI(
        base_url=os.environ[url_var],
        api_key=os.environ[key_var],
        timeout=120.0,
        max_retries=2,
    )
    backend = LoggedBackend(
        OpenAICompatBackend(client, backend=name, model=model),
        ROOT / ".reprofy" / "usage.jsonl",
    )
    modules = build_repo_map(ROOT / "examples/shop", ["shop"])
    result = classify_report(backend, REPORT, modules)

    print(f"[{name} / {model}]")
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "baseten")