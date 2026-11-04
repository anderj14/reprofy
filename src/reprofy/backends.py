"""Resolve per-step models from environment references without persisting credentials."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values
from openai import OpenAI

from .config import ConfigError, ProjectConfig, resolve_model_name
from .models.base import ModelBackend
from .models.openai_compat import OpenAICompatBackend
from .models.usage import LoggedBackend


PROVIDERS = {
    "meta": ("META_BASE_URL", "MODEL_API_KEY", "https://api.meta.ai/v1"),
    "baseten": ("BASETEN_BASE_URL", "BASETEN_API_KEY", "https://inference.baseten.co/v1"),
}


@dataclass
class StepBackends:
    classify: ModelBackend
    repro: ModelBackend
    fix: ModelBackend
    infer_expected: ModelBackend | None = None
    review: ModelBackend | None = None
    repro_fallback: ModelBackend | None = None
    review_fallback: ModelBackend | None = None
    clients: tuple = ()
    models: dict[str, dict[str, str]] = field(default_factory=dict)

    def close(self) -> None:
        for client in self.clients:
            client.close()


def build_step_backends(
    config: ProjectConfig, project: Path, *, bug_id: str, run_id: str,
    env_file: Path | None = None, environment: Mapping[str, str] | None = None,
) -> StepBackends:
    if env_file is None:
        candidates = [project / ".env", Path.cwd() / ".env"]
        env_file = next((path for path in candidates if path.is_file()), None)
    elif not env_file.is_file():
        raise ConfigError("Requested environment file does not exist")
    values = dotenv_values(env_file, interpolate=False) if env_file else {}
    values = {**{key: value for key, value in values.items() if value is not None}, **(os.environ if environment is None else environment)}
    settings = {}
    step_names = ["classify", "repro", "fix"]
    if config.models.repro_fallback is not None:
        step_names.append("repro_fallback")
    for name in step_names:
        step = getattr(config.models, name)
        if step is None:
            continue
        model = resolve_model_name(step, values)
        if model.endswith("-contributor"):
            raise ConfigError("Contributor models are outside this project's approved model configuration")
        url_var, key_var, default_url = PROVIDERS[step.backend]
        key = values.get(key_var, "").strip()
        if not key:
            raise ConfigError(f"Missing provider credential variable: {key_var}")
        settings[name] = (step.backend, model, values.get(url_var) or default_url, key)
    clients = []
    wrapped = {}
    try:
        for name, (backend, model, url, key) in settings.items():
            client = OpenAI(base_url=url, api_key=key, timeout=120.0, max_retries=0)
            clients.append(client)
            wrapped[name] = LoggedBackend(
                OpenAICompatBackend(client, backend=backend, model=model),
                project / ".reprofy/usage.jsonl",
                metadata={"bug_id": bug_id, "step": name, "run_id": run_id},
            )
    except Exception:
        for client in clients:
            client.close()
        raise ConfigError("Provider client configuration failed; check environment settings") from None
    wrapped["review"] = LoggedBackend(
        OpenAICompatBackend(clients[1], backend=settings["repro"][0], model=settings["repro"][1]),
        project / ".reprofy/usage.jsonl",
        metadata={"bug_id": bug_id, "step": "repro_review", "run_id": run_id},
    )
    if "repro_fallback" in settings:
        wrapped["review_fallback"] = LoggedBackend(
            OpenAICompatBackend(clients[-1], backend=settings["repro_fallback"][0], model=settings["repro_fallback"][1]),
            project / ".reprofy/usage.jsonl",
            metadata={"bug_id": bug_id, "step": "repro_review_fallback", "run_id": run_id},
        )
    wrapped["infer_expected"] = LoggedBackend(
        OpenAICompatBackend(clients[1], backend=settings["repro"][0], model=settings["repro"][1]),
        project / ".reprofy/usage.jsonl",
        metadata={"bug_id": bug_id, "step": "infer_expected", "run_id": run_id},
    )
    return StepBackends(**wrapped, clients=tuple(clients), models={
        name: {"backend": values[0], "model": values[1]} for name, values in settings.items()
    } | {
        "infer_expected": {"backend": settings["repro"][0], "model": settings["repro"][1]},
        "review": {"backend": settings["repro"][0], "model": settings["repro"][1]},
        **({"review_fallback": {"backend": settings["repro_fallback"][0], "model": settings["repro_fallback"][1]}} if "repro_fallback" in settings else {}),
    })
