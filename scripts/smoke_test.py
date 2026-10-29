import json
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI 

load_dotenv()

BACKENDS = {
    "baseten": ("BASETEN_BASE_URL", "BASETEN_API_KEY", "BASETEN_MODEL"),
    "meta": ("META_BASE_URL", "MODEL_API_KEY", "META_MODEL"),
}

name = sys.argv[1] if len(sys.argv) > 1 else "baseten"
url_var, key_var, model_var = BACKENDS[name]

model = sys.argv[2] if len(sys.argv) > 2 else os.environ[model_var]
client = OpenAI(base_url=os.environ[url_var], api_key=os.environ[key_var])
response = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "Reply with one short sentence: what is a regression test?"}],
    max_tokens=500,
)

print(json.dumps(response.choices[0].message.model_dump(), indent=2, ensure_ascii=False))
print("finish_reason:", response.choices[0].finish_reason)
print("usage:", response.usage)
print("backend:", name, "| model returned:", response.model)