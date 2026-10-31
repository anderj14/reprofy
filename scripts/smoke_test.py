import json
import os
import sys
from pathlib import Path

from reprofy.models.openai_compat import OpenAICompatBackend

from dotenv import load_dotenv
from openai import OpenAI 

from reprofy.models.usage import LoggedBackend

load_dotenv()

BACKENDS = {
    "baseten": ("BASETEN_BASE_URL", "BASETEN_API_KEY", "BASETEN_MODEL"),
    "meta": ("META_BASE_URL", "MODEL_API_KEY", "META_MODEL"),
}

name = sys.argv[1] if len(sys.argv) > 1 else "baseten"
url_var, key_var, model_var = BACKENDS[name]

model = sys.argv[2] if len(sys.argv) > 2 else os.environ[model_var]

client = OpenAI(
    base_url=os.environ[url_var],
    api_key=os.environ[key_var],
    timeout=120.0,
    max_retries=2
)
backend = LoggedBackend(
    OpenAICompatBackend(client, backend=name, model=model),
    Path(".reprofy/usage.jsonl"),
)

result = backend.complete(
    [{
        "role": "user",
        "content": "Reply with one short sentence: what is a regression test?",
    }],
    reasoning_effort="low",
    max_completion_tokens=2048,
)

print(json.dumps({
    "content": result.content,
    "reasoning_characters": len(result.reasoning or ""),
    "finish_reason": result.finish_reason,
    "prompt_tokens": result.prompt_tokens,
    "completion_tokens": result.completion_tokens,
    "reasoning_tokens": result.reasoning_tokens,
    "backend": result.backend,
    "model": result.model,
    "latency_seconds": round(result.latency_seconds, 2),
}, indent=2, ensure_ascii=False))