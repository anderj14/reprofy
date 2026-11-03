"""Create a clean, disposable project for a Reprofy walkthrough."""

import argparse
import shutil
from pathlib import Path

from reprofy.bugs import BugReport
from reprofy.config import initialize_project
from reprofy.store import YamlBugStore


def prepare_demo(project: Path, *, backend: str = "meta") -> tuple[Path, str]:
    project = project.expanduser().resolve()
    if project.exists() or project.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing path: {project}")
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/buggy_shop"
    project.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fixture, project)
    initialize_project(project, source_dirs=["shop"], tests_dir="tests", backend=backend)
    bug = YamlBugStore(project).create(BugReport(
        text="La última página de productos repite un artículo.",
        expected="Cada producto debe aparecer una sola vez al recorrer todas las páginas.",
    ))
    return project, bug.id


def main():
    parser = argparse.ArgumentParser(description="Prepare a clean Reprofy shop demo without model calls")
    parser.add_argument("--project", type=Path, required=True, help="New directory to create; it must not already exist")
    parser.add_argument("--backend", choices=["meta", "baseten"], default="meta")
    args = parser.parse_args()
    try:
        project, bug_id = prepare_demo(args.project, backend=args.backend)
    except (FileExistsError, OSError, ValueError) as error:
        parser.error(str(error))
    print(f"PROJECT: {project}")
    print(f"BUG_ID: {bug_id}")
    print("READY: clean shop fixture created; no model call was made")


if __name__ == "__main__":
    main()
