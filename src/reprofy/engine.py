"""Configured, persistent report-to-proposal orchestration. Applying remains separate."""

import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from uuid import uuid4

from .applying import create_apply_proposal
from .backends import StepBackends
from .bugs import BugClassification, BugFix, BugReproduction
from .classification import classify_report
from .config import ProjectConfig, load_config, project_path
from .context import gather_context
from .fixing import propose_verified_fix
from .models.structured import StructuredOutputError
from .repo_map import build_repo_map
from .reproduction import generate_reproduction
from .repro_validation import assess_reproduction, review_reproduction, static_reproduction_issue
from .store import StoreError, YamlBugStore
from .test_runner import run_pytest, summarize_test_run
from .workspace import project_hashes, protected_files, temporary_workspace


class EngineError(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowResult:
    bug_id: str
    status: str
    artifact_dir: str
    diff: str = ""
    error: str | None = None
    apply_file: str | None = None


def _related_tests(snapshot: Path, tests_dir: str, suspected: list[str]) -> list[str]:
    selected = []
    module_names = {".".join(PurePosixPath(path).with_suffix("").parts) for path in suspected}
    candidates = []
    for relative, content in protected_files(snapshot).items():
        path = PurePosixPath(relative)
        if path.suffix == ".py" and (path.name.startswith("test_") or path.name.endswith("_test.py")):
            if tests_dir == "." or path.is_relative_to(PurePosixPath(tests_dir)) or path.is_relative_to(PurePosixPath("tests/repro")):
                candidates.append((relative, content))
    if len(candidates) > 200:
        raise EngineError("More than 200 test files; configure a smaller tests_dir for this version")
    for relative, content in sorted(candidates):
        if len(content) > 32_000:
            continue
        try:
            tree = ast.parse(content.decode("utf-8"))
        except (SyntaxError, UnicodeError):
            continue
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
            elif isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
        if any(left == right or left.startswith(right + ".") or right.startswith(left + ".") for left in imports for right in module_names):
            selected.append(relative)
    # A small fallback provides pytest/import conventions for unfamiliar repositories.
    return (selected or [path for path, content in sorted(candidates) if len(content) <= 32_000])[:10]


def fix_bug(
    project: Path, bug_id: str, backends: StepBackends, *,
    config: ProjectConfig | None = None, run_id: str | None = None,
    max_attempts_override: int | None = None,
    on_event: Callable[[str], None] | None = None,
) -> WorkflowResult:
    project = project.resolve()
    config = load_config(project) if config is None else config
    store = YamlBugStore(project)
    bug = store.get(bug_id)
    if bug.status != "new":
        raise EngineError("fix requires a new bug; inspect existing results or create a new report")
    if not bug.report.expected:
        raise EngineError("Expected behavior is required; supply --expected before fixing this report")
    if max_attempts_override is not None and not 1 <= max_attempts_override <= 10:
        raise EngineError("max_attempts_override must be between 1 and 10")
    max_attempts = config.max_attempts if max_attempts_override is None else max_attempts_override
    run_id = uuid4().hex if run_id is None else run_id
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise EngineError("Invalid run identifier")
    relative_dir = f".reprofy/proposals/{bug.id}/{run_id}"
    artifact = project_path(project, relative_dir)
    artifact.mkdir(parents=True, exist_ok=False)
    (artifact / "run.json").write_text(json.dumps({
        "bug_id": bug.id, "run_id": run_id,
        "report": bug.report.model_dump(), "models": backends.models,
        "configuration": config.model_dump(mode="json"),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    reproduction_path = f"tests/repro/test_{bug.id.lower().replace('-', '_')}.py"

    def event(message: str) -> None:
        if on_event:
            on_event(message)

    def save(**updates):
        nonlocal bug
        bug = store.save(bug.model_copy(update=updates))
        event(f"{bug.id}: {bug.status}")

    def finish_failed(status: str, message: str, *, terminal: bool = True) -> WorkflowResult:
        save(status="failed" if terminal else bug.status, failure=message[:4000])
        (artifact / "failure.json").write_text(json.dumps({"status": status, "message": message}, indent=2), encoding="utf-8")
        return WorkflowResult(bug.id, status, str(artifact), error=message)

    def note_repro_fallback(stage: str, error: StructuredOutputError) -> None:
        primary = backends.models.get("repro", {}).get("model", "primary model")
        fallback = backends.models.get("repro_fallback", {}).get("model", "fallback model")
        detail = " ".join(str(error).split())[:180]
        event(f"{bug.id}: {stage} output from {primary} was invalid; retrying with {fallback}. {detail}")

    try:
        with temporary_workspace(project) as snapshot:
            if load_config(snapshot) != config:
                raise EngineError("Configuration changed while taking the snapshot; retry with current configuration")
            targets = [config.tests_dir]
            regression_dir = snapshot / "tests/repro"
            if regression_dir.is_dir() and config.tests_dir != "." and not PurePosixPath("tests/repro").is_relative_to(PurePosixPath(config.tests_dir)):
                targets.append("tests/repro")
            initial = project_hashes(snapshot)
            event(f"{bug.id}: running the existing test suite before asking the models")
            baseline = run_pytest(snapshot, targets, timeout_seconds=config.test_timeout_seconds, test_command=config.test_command, project_root=project)
            (artifact / "baseline.txt").write_text(baseline.output, encoding="utf-8")
            if summarize_test_run(baseline).status != "passed" or project_hashes(snapshot) != initial:
                return finish_failed("baseline_failed", "Baseline suite must pass unchanged before model calls; inspect baseline.txt", terminal=False)
            event(f"{bug.id}: baseline passed; mapping source files")
            modules = build_repo_map(snapshot, config.source_dirs)
            modules = {
                name: path for name, path in modules.items()
                if not PurePosixPath(path).name.startswith("test_")
                and not PurePosixPath(path).name.endswith("_test.py")
                and PurePosixPath(path).name != "conftest.py"
                and not (config.tests_dir != "." and PurePosixPath(path).is_relative_to(PurePosixPath(config.tests_dir)))
                and not PurePosixPath(path).is_relative_to(PurePosixPath("tests/repro"))
            }
            if not modules:
                return finish_failed("no_source", "No Python source modules found in configured source_dirs", terminal=False)
            if (snapshot / reproduction_path).exists():
                return finish_failed("reproduction_exists", "Regression path already exists; refusing to overwrite it", terminal=False)
            event(f"{bug.id}: baseline passed; classifying")
            report_data = json.dumps(bug.report.model_dump(exclude_none=True), ensure_ascii=False)
            step = config.models.classify
            classification = classify_report(
                backends.classify, report_data, modules,
                reasoning_effort=step.reasoning_effort, max_completion_tokens=step.max_completion_tokens,
            )
            classified = BugClassification.model_validate(classification.model_dump(mode="json"))
            save(status="classified", classification=classified, failure=None)
            tests = _related_tests(snapshot, config.tests_dir, classified.suspected_files)
            context = gather_context(snapshot, [*classified.suspected_files, *tests])
            review_backend = backends.review or backends.repro
            review_context = gather_context(snapshot, classified.suspected_files)
            accepted = None
            feedback = []
            repro_step = config.models.repro
            for number in range(1, 4):
                event(f"{bug.id}: generating reproduction test ({number}/3)")
                run = None
                attempt_dir = artifact / "reproduction-attempts" / f"{number:02d}"
                attempt_dir.mkdir(parents=True)
                reproduction = generate_reproduction(
                    backends.repro, report_data, context, expected_behavior=bug.report.expected,
                    fallback_backend=backends.repro_fallback,
                    on_fallback=lambda error: note_repro_fallback("Reproduction", error),
                    reasoning_effort=repro_step.reasoning_effort,
                    max_completion_tokens=repro_step.max_completion_tokens,
                    feedback=feedback,
                )
                (attempt_dir / "reproduction.py").write_text(reproduction.test_code, encoding="utf-8")
                (attempt_dir / "reproduction.json").write_text(reproduction.model_dump_json(indent=2), encoding="utf-8")
                issue = None
                if not (issue := static_reproduction_issue(reproduction.test_code)):
                    event(f"{bug.id}: running reproduction test ({number}/3)")
                    with temporary_workspace(snapshot) as repro_workspace:
                        repro_target = repro_workspace / reproduction_path
                        repro_target.parent.mkdir(parents=True, exist_ok=True)
                        repro_target.write_text(reproduction.test_code, encoding="utf-8")
                        before_run = protected_files(repro_workspace)
                        run = run_pytest(
                            repro_workspace, [reproduction_path],
                            timeout_seconds=config.test_timeout_seconds, test_command=config.test_command,
                            source_files=sorted(modules.values()), project_root=project,
                        )
                        unchanged = protected_files(repro_workspace) == before_run
                    eligible, issue = assess_reproduction(
                        run, test_path=reproduction_path, source_files=sorted(modules.values()),
                        code=reproduction.test_code,
                    )
                    if not unchanged:
                        eligible, issue = False, "Generated test changed protected project files"
                    (attempt_dir / "pytest.txt").write_text(run.output, encoding="utf-8")
                    if run.evidence is not None:
                        (attempt_dir / "evidence.json").write_text(json.dumps(run.evidence, indent=2, ensure_ascii=False), encoding="utf-8")
                    if eligible:
                        event(f"{bug.id}: reproduction failed against the current code; checking that it matches the report")
                        review_step = config.models.repro
                        review = review_reproduction(
                            review_backend, report=bug.report.text,
                            expected_behavior=bug.report.expected, reproduction=reproduction,
                            evidence=run.evidence, source_context=review_context,
                            reasoning_effort=review_step.reasoning_effort,
                            max_completion_tokens=review_step.max_completion_tokens,
                            fallback_backend=backends.review_fallback or backends.repro_fallback,
                            on_fallback=lambda error: note_repro_fallback("Semantic review", error),
                        )
                        (attempt_dir / "review.json").write_text(review.model_dump_json(indent=2), encoding="utf-8")
                        if review.verdict == "accept":
                            event(f"{bug.id}: reproduction accepted; starting fix attempts")
                            accepted = (reproduction, run, review, number)
                            break
                        issue = f"Semantic review {review.verdict}: {review.reason}; observed cause: {review.observed_cause}"
                        if review.verdict == "needs_clarification":
                            feedback.append(json.dumps({
                                "attempt": number, "test_code": reproduction.test_code,
                                "feedback": issue,
                            }, ensure_ascii=False)[:12_000])
                            break
                if issue is None:
                    issue = "Reproduction was not accepted"
                feedback.append(json.dumps({
                    "attempt": number, "test_code": reproduction.test_code,
                    "feedback": issue,
                    "pytest_output": run.output[:4_000] if run is not None else None,
                }, ensure_ascii=False)[:12_000])
                (attempt_dir / "feedback.txt").write_text(feedback[-1], encoding="utf-8")
            if accepted is None:
                reason = feedback[-1] if feedback else "No reproduction attempt was accepted"
                return finish_failed("needs_clarification" if "needs_clarification" in reason else "invalid_reproduction", reason)
            reproduction, accepted_run, accepted_review, repro_attempts = accepted
            (artifact / "reproduction.py").write_text(reproduction.test_code, encoding="utf-8")
            (artifact / "reproduction.json").write_text(reproduction.model_dump_json(indent=2), encoding="utf-8")
            (artifact / "repro-failure.txt").write_text(accepted_run.output, encoding="utf-8")
            (artifact / "repro-review.json").write_text(accepted_review.model_dump_json(indent=2), encoding="utf-8")

            def reproduction_validator(run, expected_status):
                actual_status = summarize_test_run(run).status
                required_status = "test_failed" if expected_status == "failed" else expected_status
                if actual_status != required_status or run.evidence_error or not run.evidence:
                    return False
                call_reports = [r for r in run.evidence.get("reports", []) if r.get("phase") == "call"]
                return len(call_reports) == 1 and bool(set(sorted(modules.values())).intersection(call_reports[0].get("called_files", [])))

            def reproduced(run):
                (artifact / "repro-failure.txt").write_text(run.output, encoding="utf-8")
                save(status="repro_written", repro=BugReproduction(
                    test_file=reproduction_path, fails_as_expected=False,
                    expected_failure=reproduction.expected_failure,
                    attempts=repro_attempts, validation="accepted",
                    review_reason=accepted_review.reason,
                    observed_cause=accepted_review.observed_cause,
                    evidence_file=f"{relative_dir}/reproduction-attempts/{repro_attempts:02d}/evidence.json",
                ))

            def attempt(number):
                save(status="fix_proposed", fix=BugFix(attempts=number))

            step = config.models.fix
            result = propose_verified_fix(
                backends.fix, snapshot, report=bug.report.text,
                expected_behavior=bug.report.expected, reproduction=reproduction,
                source_files=sorted(modules.values()), context_files=tests,
                suite_targets=targets, max_attempts=max_attempts,
                timeout_seconds=config.test_timeout_seconds, test_command=config.test_command,
                reproduction_path=reproduction_path,
                reasoning_effort=step.reasoning_effort, max_completion_tokens=step.max_completion_tokens,
                on_reproduction=reproduced, on_attempt=attempt,
                reproduction_validator=reproduction_validator,
                on_event=event,
            )
            (artifact / "result.json").write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
            if result.status != "tests_verified":
                return finish_failed(result.status, "No verified fix produced; inspect result.json and reproduction artifacts")
            if result.explanation:
                event(f"{bug.id}: proposed change summary: {' '.join(result.explanation.split())[:300]}")
        proposal = create_apply_proposal(project, result, report=bug.report.text, expected_behavior=bug.report.expected)
        proposal = proposal.model_copy(update={"bug_id": bug.id, "bug_revision": bug.revision + 1})
        (artifact / "proposal.patch").write_text(result.diff, encoding="utf-8")
        (artifact / "apply.json").write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
        save(status="fix_verified", fix=BugFix(
            attempts=result.attempts, diff_file=f"{relative_dir}/proposal.patch",
            proposal_file=f"{relative_dir}/apply.json", tests_pass=True,
        ))
        event(f"{bug.id}: tests passed; review reproduction meaning and diff before approval")
        return WorkflowResult(bug.id, "fix_verified", str(artifact), result.diff, apply_file=str(artifact / "apply.json"))
    except StoreError:
        raise
    except KeyboardInterrupt:
        finish_failed("interrupted", "Workflow interrupted before approval; original source was not patched")
        raise
    except Exception as error:
        message = (
            f"Workflow error ({type(error).__name__}): {error}"[:2000]
            if isinstance(error, (EngineError, StructuredOutputError))
            else f"Workflow error ({type(error).__name__}); inspect local artifacts and provider settings"
        )
        return finish_failed("failed", message)
