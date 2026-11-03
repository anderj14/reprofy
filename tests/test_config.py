from unittest.mock import patch

import pytest
from pydantic import ValidationError

from reprofy.cli import main
from reprofy.config import (
    ConfigError, ModelStep, ProjectConfig, dump_config, initialize_project,
    load_config, resolve_model_name,
)


def test_init_roundtrip_preserves_state_and_uses_environment_references(tmp_path, monkeypatch):
    (tmp_path / "shop").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / ".reprofy").mkdir()
    usage = tmp_path / ".reprofy/usage.jsonl"
    usage.write_text("existing metrics\n")
    (tmp_path / ".gitignore").write_text("__pycache__/")
    monkeypatch.setenv("MODEL_API_KEY", "synthetic-secret-do-not-write")
    monkeypatch.setenv("META_MODEL", "fixture-model-from-env")
    config = initialize_project(tmp_path, source_dirs=["shop"])
    assert load_config(tmp_path) == config
    assert config.models.classify.model_env == "META_MODEL"
    assert config.models.repro.reasoning_effort == "high"
    assert config.models.fix.max_completion_tokens == 8192
    assert resolve_model_name(config.models.fix) == "fixture-model-from-env"
    written = (tmp_path / "reprofy.yaml").read_text()
    assert "synthetic-secret-do-not-write" not in written
    assert "fixture-model-from-env" not in written
    assert usage.read_text() == "existing metrics\n"
    assert (tmp_path / ".reprofy/bugs").is_dir()
    assert (tmp_path / ".gitignore").read_text() == "__pycache__/\n.reprofy/\n"


def test_repeated_init_refuses_to_overwrite_configuration(tmp_path):
    initialize_project(tmp_path)
    original = (tmp_path / "reprofy.yaml").read_bytes()
    with pytest.raises(ConfigError, match="already exists"):
        initialize_project(tmp_path, backend="baseten")
    assert (tmp_path / "reprofy.yaml").read_bytes() == original


def test_baseten_config_uses_baseten_model_variable(tmp_path):
    (tmp_path / "src").mkdir()
    config = initialize_project(tmp_path, backend="baseten")
    assert config.source_dirs == ["src"]
    for step in [config.models.classify, config.models.repro, config.models.fix]:
        assert step.backend == "baseten"
        assert step.model_env == "BASETEN_MODEL"


@pytest.mark.parametrize("text", [
    "max_attempts: 3\nmax_attempts: 5\n",
    "language: [\n",
    "- not a mapping\n",
    "!!python/object/apply:os.system ['echo unexpected']\n",
])
def test_invalid_or_unsafe_yaml_is_rejected(tmp_path, text):
    (tmp_path / "reprofy.yaml").write_text(text)
    with pytest.raises(ConfigError):
        load_config(tmp_path)


@pytest.mark.parametrize("field, value", [
    ("max_attempts", 0), ("max_attempts", "3"),
    ("test_timeout_seconds", -1), ("test_timeout_seconds", True),
    ("language", "javascript"), ("source_dirs", []),
    ("source_dirs", ["src", "src"]), ("test_command", ["bash", "-c", "pytest"]),
])
def test_invalid_configuration_fields_are_rejected(field, value):
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate({field: value})


@pytest.mark.parametrize("directory", ["../outside", "/tmp", "src/../src", ".reprofy", ".venv"])
def test_invalid_source_paths_do_not_initialize_project(tmp_path, directory):
    with pytest.raises(ConfigError):
        initialize_project(tmp_path, source_dirs=[directory])
    assert not (tmp_path / "reprofy.yaml").exists()


def test_source_and_state_symlinks_are_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ConfigError, match="symlinks"):
        initialize_project(root, source_dirs=["src"])
    (root / "src").unlink()
    (root / ".reprofy").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ConfigError, match="symlinks"):
        initialize_project(root, source_dirs=["."])
    assert not list(outside.iterdir())


def test_missing_models_are_checked_only_when_resolution_is_requested(tmp_path):
    config = initialize_project(tmp_path)
    assert load_config(tmp_path) == config
    with pytest.raises(ConfigError, match="META_MODEL"):
        resolve_model_name(config.models.fix, {})
    with pytest.raises(ValidationError):
        ModelStep(model_env="MODEL_API_KEY")


def test_cli_init_and_config_are_offline_and_do_not_expose_unknown_values(tmp_path, capsys):
    with patch("openai.OpenAI", side_effect=AssertionError("Unexpected client creation")):
        assert main(["init", "--project", str(tmp_path)]) == 0
        assert main(["config", "--project", str(tmp_path)]) == 0
        assert "CONFIG OK" in capsys.readouterr().out
        assert main(["init", "--project", str(tmp_path)]) == 1
        assert "already exists" in capsys.readouterr().err
        with (tmp_path / "reprofy.yaml").open("a") as stream:
            stream.write("api_key: synthetic-private-value\n")
        assert main(["config", "--project", str(tmp_path)]) == 1
        error = capsys.readouterr().err
        assert "extra_forbidden" in error
        assert "synthetic-private-value" not in error


def test_oversized_configuration_is_rejected(tmp_path):
    (tmp_path / "reprofy.yaml").write_text("#" * 65_537)
    with pytest.raises(ConfigError, match="exceeds"):
        load_config(tmp_path)
