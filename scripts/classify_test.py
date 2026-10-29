import json
import os
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BACKENDS = {
    "baseten": ("BASETEN_BASE_URL", "BASETEN_API_KEY", "BASETEN_MODEL"),
    "meta": ("META_BASE_URL", "MODEL_API_KEY", "META_MODEL"),
}
CONFIGS = [("meta", "muse-spark-1.3"), ("meta", "muse-wonder-1.0"), ("baseten", None)]

SCHEMA = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "area": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["severity", "area", "confidence"],
    "additionalProperties": False,
}

REPORT = "Cuando aplico un cupón de 100% el total sale negativo"


def run(name, model_override):
    url_var, key_var, model_var = BACKENDS[name]
    model = model_override or os.environ[model_var]
    client = OpenAI(base_url=os.environ[url_var], api_key=os.environ[key_var])
    start = time.time()
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Classify the bug report. Answer only with the JSON object."},
                {"role": "user", "content": REPORT},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "BugClassification", "schema": SCHEMA},
            },
            reasoning_effort="low",
            max_completion_tokens=4000,
        )
    except Exception as e:
        print(f"[{name} / {model}] ERROR: {e}\n")
        return
    choice = r.choices[0]
    details = r.usage.completion_tokens_details
    print(f"[{name} / {r.model}]")
    print("content:", choice.message.content)
    
    print("finish_reason:", choice.finish_reason)
    print(
        "tokens: prompt", r.usage.prompt_tokens,
        "| completion", r.usage.completion_tokens,
        "| reasoning", details.reasoning_tokens if details else None,
        "| seconds", round(time.time() - start, 1),
    )
    try:
        json.loads(choice.message.content)
        print("valid JSON: yes\n")
    except Exception:
        print("valid JSON: NO\n")


for cfg in CONFIGS:
    run(*cfg)