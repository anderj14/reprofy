"""Validated project configuration; credentials and model values stay in the environment."""

import os
from pathlib import Path, PurePosixPath
from typing import Literal, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .yaml_io import YamlDataError, parse_yaml_mapping


class ConfigError(ValueError):
    pass


class ModelStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    backend: Literal["meta", "baseten"] = "meta"
    model_env: str = Field(default="META_MODEL", pattern=r"^[A-Z][A-Z0-9_]*_MODEL$")
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh", "max"] = "low"
    max_completion_tokens: int = Field(default=2048, ge=128, le=32768)


class StepModels(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    classify: ModelStep = Field(default_factory=ModelStep)
    repro: ModelStep = Field(default_factory=lambda: ModelStep(reasoning_effort="high", max_completion_tokens=8192))
    fix: ModelStep = Field(default_factory=lambda: ModelStep(reasoning_effort="high", max_completion_tokens=8192))


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    language: Literal["python"] = "python"
    test_command: list[str] = Field(default_factory=lambda: ["{python}", "-m", "pytest"], min_length=3)
    source_dirs: list[str] = Field(default_factory=lambda: ["src"], min_length=1)
    tests_dir: str = "tests"
    max_attempts: int = Field(default=3, ge=1, le=5)
    test_timeout_seconds: int = Field(default=30, ge=1, le=300)
    models: StepModels = Field(default_factory=StepModels)

    @field_validator("test_command")
    @classmethod
    def pytest_command(cls, value: list[str]) -> list[str]:
        if value[:3] != ["{python}", "-m", "pytest"]:
            raise ValueError("test_command must start with ['{python}', '-m', 'pytest']")
        return value

    @field_validator("source_dirs")
    @classmethod
    def unique_sources(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("source_dirs must not contain duplicates")
        return value


def project_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    parsed = PurePosixPath(relative)
    if not relative or parsed.is_absolute() or ".." in parsed.parts or "\\" in relative or parsed.as_posix() != relative:
        raise ConfigError("Configured paths must be canonical relative project paths")
    path = root
    for part in parsed.parts:
        path = path / part
        if path.is_symlink():
            raise ConfigError("Configured paths must not contain symlinks")
    if not path.resolve().is_relative_to(root):
        raise ConfigError("Configured path escapes project")
    return path


def validate_project_paths(root: Path, config: ProjectConfig) -> None:
    for directory in config.source_dirs:
        if PurePosixPath(directory).parts[:1] in {(".git",), (".venv",), (".reprofy",)}:
            raise ConfigError("Private state and environment directories cannot be source_dirs")
        if not project_path(root, directory).is_dir():
            raise ConfigError(f"Source directory does not exist: {directory}")
    tests = project_path(root, config.tests_dir)
    if tests.exists() and not tests.is_dir():
        raise ConfigError("tests_dir must be a directory")


def dump_config(config: ProjectConfig) -> str:
    return yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


def load_config(root: Path) -> ProjectConfig:
    path = project_path(root, "reprofy.yaml")
    if not path.is_file():
        raise ConfigError("reprofy.yaml not found; run reprofy init first")
    with path.open(encoding="utf-8") as stream:
        text = stream.read(65_537)
    if len(text) > 65_536:
        raise ConfigError("reprofy.yaml exceeds the 65,536-character limit")
    try:
        raw = parse_yaml_mapping(text)
    except YamlDataError as error:
        raise ConfigError(f"Invalid reprofy.yaml: {error}") from error
    config = ProjectConfig.model_validate(raw)
    validate_project_paths(root, config)
    return config


def initialize_project(
    root: Path, *, source_dirs: list[str] | None = None,
    tests_dir: str = "tests", backend: Literal["meta", "baseten"] = "meta",
) -> ProjectConfig:
    root = root.resolve()
    if not root.is_dir():
        raise ConfigError("Project directory does not exist")
    path = project_path(root, "reprofy.yaml")
    if path.exists():
        raise ConfigError("reprofy.yaml already exists; initialization will not overwrite it")
    model_env = "META_MODEL" if backend == "meta" else "BASETEN_MODEL"
    config = ProjectConfig(
        source_dirs=source_dirs if source_dirs is not None else ["src" if (root / "src").is_dir() else "."],
        tests_dir=tests_dir,
        models=StepModels(**{
            step: ModelStep(
                backend=backend, model_env=model_env,
                reasoning_effort="low" if step == "classify" else "high",
                max_completion_tokens=2048 if step == "classify" else 8192,
            ) for step in ["classify", "repro", "fix"]
        }),
    )
    validate_project_paths(root, config)
    state_dirs = [project_path(root, name) for name in [".reprofy", ".reprofy/bugs", ".reprofy/proposals"]]
    for state in state_dirs:
        if state.exists() and not state.is_dir():
            raise ConfigError("Reprofy state paths must be directories")
    ignore = project_path(root, ".gitignore")
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    for state in state_dirs:
        state.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents repeated/concurrent init from overwriting config.
    with path.open("x", encoding="utf-8") as stream:
        stream.write(dump_config(config))
    if not {".reprofy/", "/.reprofy/"}.intersection(existing.splitlines()):
        with ignore.open("a", encoding="utf-8") as stream:
            stream.write(("\n" if existing and not existing.endswith("\n") else "") + ".reprofy/\n")
    return config


def resolve_model_name(step: ModelStep, environment: Mapping[str, str] | None = None) -> str:
    environment = os.environ if environment is None else environment
    model = environment.get(step.model_env, "").strip()
    if not model:
        raise ConfigError(f"Missing model environment variable: {step.model_env}")
    return model
