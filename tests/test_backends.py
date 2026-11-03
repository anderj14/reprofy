from unittest.mock import Mock

import pytest

from reprofy.backends import build_step_backends
from reprofy.config import ConfigError, ModelStep, ProjectConfig


def test_mixed_step_models_are_resolved_from_env_without_mutating_global_environment(tmp_path, monkeypatch):
    env = tmp_path / "local.env"
    env.write_text("META_MODEL=meta-fixture\nMODEL_API_KEY=synthetic-meta-key\nBASETEN_MODEL=open-fixture\nBASETEN_API_KEY=synthetic-open-key\n")
    config = ProjectConfig()
    config.models.repro = ModelStep(backend="baseten", model_env="BASETEN_MODEL", reasoning_effort="high")
    config.models.fix = config.models.repro
    constructor = Mock(side_effect=lambda **kwargs: Mock())
    monkeypatch.setattr("reprofy.backends.OpenAI", constructor)
    steps = build_step_backends(config, tmp_path, bug_id="BUG-001", run_id="fixture", env_file=env, environment={"META_MODEL": "override-model"})
    assert steps.classify.backend.model == "override-model"
    assert steps.repro.backend.model == steps.fix.backend.model == "open-fixture"
    assert steps.fix.metadata == {"bug_id": "BUG-001", "step": "fix", "run_id": "fixture"}
    assert constructor.call_args_list[0].kwargs["api_key"] == "synthetic-meta-key"
    assert constructor.call_args_list[1].kwargs["api_key"] == "synthetic-open-key"
    steps.close()
    for client in steps.clients:
        client.close.assert_called_once()


def test_missing_credential_stops_before_client_creation(tmp_path, monkeypatch):
    env = tmp_path / "local.env"
    env.write_text("META_MODEL=fixture\n")
    constructor = Mock()
    monkeypatch.setattr("reprofy.backends.OpenAI", constructor)
    with pytest.raises(ConfigError, match="MODEL_API_KEY"):
        build_step_backends(ProjectConfig(), tmp_path, bug_id="BUG-001", run_id="fixture", env_file=env, environment={})
    constructor.assert_not_called()


def test_contributor_model_is_rejected_before_client_creation(tmp_path, monkeypatch):
    env = tmp_path / "local.env"
    env.write_text("META_MODEL=muse-spark-1.3-contributor\nMODEL_API_KEY=synthetic-key\n")
    constructor = Mock()
    monkeypatch.setattr("reprofy.backends.OpenAI", constructor)
    with pytest.raises(ConfigError, match="Contributor"):
        build_step_backends(ProjectConfig(), tmp_path, bug_id="BUG-001", run_id="fixture", env_file=env, environment={})
    constructor.assert_not_called()
