"""Project configuration, persisted bug reports and approved application."""

import argparse
import json
import re
import shlex
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from .applying import (
    ApplyError, apply_proposal, load_apply_proposal, prepare_application, verify_application,
)
from .config import (
    ConfigError, dump_config, initialize_project, load_automation_mode, load_config,
    project_path, save_automation_mode,
)
from .bugs import BUG_STATUSES, BugReport
from .store import StoreError, YamlBugStore, dump_bug
from .backends import build_step_backends
from .engine import EngineError, fix_bug
from .models.usage import usage_by_step
from .models.usage import LoggedBackend
from .post_apply import analyze_post_apply_failure, run_applied_suite, test_run_passed


def _paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def _progress(message: str) -> None:
    if ": " in message:
        stage, detail = message.split(": ", 1)
        print(f"  {_paint('●', '36')} {_paint(stage, '1')}  {_paint(detail, '2')}", file=sys.stderr, flush=True)
    else:
        print(f"  {_paint('●', '36')} {message}", file=sys.stderr, flush=True)


def _print_diff(diff: str) -> None:
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            print(_paint(line, "32"))
        elif line.startswith("-") and not line.startswith("---"):
            print(_paint(line, "31"))
        elif line.startswith(("+++", "---", "diff ")):
            print(_paint(line, "1;36"))
        elif line.startswith("@@"):
            print(_paint(line, "36"))
        else:
            print(line)


def _print_bug_usage(project: Path, bug_id: str) -> None:
    usage = usage_by_step(project / ".reprofy/usage.jsonl", bug_id)
    print(f"\n{_paint('╭─ MODEL USAGE', '1;36')}  {bug_id}")
    if not usage:
        print("│  No model usage has been recorded for this report.")
        print(_paint("╰─", '1;36'))
        return
    order = ("classify", "repro", "repro_review", "fix")
    header = f"{'STEP':<14} {'CALLS':>5} {'INPUT':>10} {'OUTPUT':>10} {'TOTAL':>10} {'REASONING':>10} {'TIME':>8}"
    print(f"  {_paint(header, '1')}")
    all_values = []
    for step in (*order, *sorted(set(usage) - set(order))):
        values = usage.get(step)
        if not values:
            continue
        prompt = int(values["prompt_tokens"])
        completion = int(values["completion_tokens"])
        reasoning = int(values["reasoning_tokens"])
        calls = int(values["calls"])
        latency = float(values["latency_seconds"])
        all_values.append(values)
        row = f"{step:<14} {calls:>5} {prompt:>10,} {completion:>10,} {prompt + completion:>10,} {reasoning:>10,} {latency:>7.1f}s"
        print(f"  {_paint(row, '36')}")
    prompt_sum = sum(int(value["prompt_tokens"]) for value in all_values)
    completion_sum = sum(int(value["completion_tokens"]) for value in all_values)
    print(f"  {_paint('─' * len(header), '2')}")
    print(f"  {_paint(f'TOTAL MODEL TOKENS: {prompt_sum + completion_sum:,}  ·  input {prompt_sum:,}  ·  output {completion_sum:,}', '1;32')}")
    print("  Apply and human review do not call a model (0 additional tokens).")
    print(_paint("╰─", '1;36'))


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(prog="reprofy")
    subcommands = parser.add_subparsers(dest="command", required=True)
    init = subcommands.add_parser("init", help="Create project configuration and local state")
    init.add_argument("--project", type=Path, default=Path.cwd())
    init.add_argument("--source-dir", action="append", dest="source_dirs")
    init.add_argument("--tests-dir", default="tests")
    init.add_argument("--backend", choices=["meta", "baseten"], default="meta")
    config = subcommands.add_parser("config", help="Validate and display project configuration")
    config.add_argument("--project", type=Path, default=Path.cwd())
    mode = subcommands.add_parser("mode", help="Choose manual or automatic report processing")
    mode.add_argument("automation_mode", choices=["manual", "automatic"])
    mode.add_argument("--project", type=Path, default=Path.cwd())
    report = subcommands.add_parser("report", help="Persist a plain-language bug report without model calls")
    report.add_argument("text")
    report.add_argument("--expected")
    report.add_argument("--actual")
    report.add_argument("--context")
    report.add_argument("--project", type=Path, default=Path.cwd())
    report.add_argument("--json", action="store_true", dest="json_output")
    listing = subcommands.add_parser("list", help="List persisted bug reports")
    listing.add_argument("--project", type=Path, default=Path.cwd())
    listing.add_argument("--status", choices=BUG_STATUSES)
    listing.add_argument("--json", action="store_true", dest="json_output")
    show = subcommands.add_parser("show", help="Show a persisted bug record")
    show.add_argument("bug_id")
    show.add_argument("--project", type=Path, default=Path.cwd())
    show.add_argument("--json", action="store_true", dest="json_output")
    delete = subcommands.add_parser("delete", help="Delete the record for a resolved, applied bug")
    delete.add_argument("bug_id")
    delete.add_argument("--project", type=Path, default=Path.cwd())
    fix = subcommands.add_parser("fix", help="Generate and verify a proposal for a persisted bug; never apply it")
    fix.add_argument("bug_id")
    fix.add_argument("--project", type=Path, default=Path.cwd())
    fix.add_argument("--env-file", type=Path)
    fix.add_argument("--expected", help="Explicit expected behavior for a new report")
    fix.add_argument("--json", action="store_true", dest="json_output")
    apply = subcommands.add_parser("apply", help="Review and apply a test-verified proposal")
    apply.add_argument("proposal", type=Path, help="Path to apply.json or a recorded BUG-ID")
    apply.add_argument("--project", type=Path, default=Path.cwd())
    apply.add_argument("--check", action="store_true", help="Verify and preview without applying")
    apply.add_argument("--env-file", type=Path, help="Environment file for model analysis if post-apply tests fail")
    worker = subcommands.add_parser("worker", help="Automatically process new reports on verified Git branches")
    worker.add_argument("--project", type=Path, default=Path.cwd())
    worker.add_argument("--once", action="store_true", help="Process at most one report and exit")
    worker.add_argument("--poll-seconds", type=float, default=3)
    worker.add_argument("--max-attempts", type=int, default=10)

    if not argv:
        if sys.stdin.isatty() and sys.stdout.isatty():
            from .interactive import run_interactive

            return run_interactive(lambda command: main(list(command)))
        parser.print_help()
        return 0

    args = parser.parse_args(argv)
    try:
        if args.command == "worker":
            from .worker import AutoWorker

            auto_worker = AutoWorker(
                args.project, poll_seconds=args.poll_seconds,
                max_attempts=args.max_attempts,
            )
            if args.once:
                print(auto_worker.run_once() or "No new reports.")
            else:
                auto_worker.run_forever()
            return 0
        if args.command == "mode":
            load_config(args.project)
            save_automation_mode(args.project, args.automation_mode)
            print(f"Automation mode: {args.automation_mode}")
            if args.automation_mode == "automatic":
                print("New reports will be processed by the worker when it is running.")
            else:
                print("New reports will wait for a developer to start the fix workflow.")
            return 0
        if args.command == "init":
            initialize_project(
                args.project, source_dirs=args.source_dirs,
                tests_dir=args.tests_dir, backend=args.backend,
            )
            print(f"\n{_paint('╭─ PROJECT INITIALIZED', '1;32')}")
            print(f"│  {_paint('PATH', '1')}  {args.project.resolve()}")
            print("│  Created reprofy.yaml and .reprofy/bugs/")
            print(_paint("╰─ Model credentials remain environment references.", '32'))
            return 0
        if args.command == "fix":
            config = load_config(args.project)
            store = YamlBugStore(args.project)
            bug = store.get(args.bug_id)
            if bug.status != "new":
                raise EngineError("fix requires a new bug; inspect existing results or create a new report")
            if args.expected is not None:
                bug = store.save(bug.model_copy(update={
                    "report": bug.report.model_copy(update={"expected": args.expected}),
                }))
            if not bug.report.expected:
                raise EngineError("Expected behavior is required; use fix BUG-ID --expected '...' ")
            run_id = uuid4().hex
            backends = build_step_backends(
                config, args.project.resolve(), bug_id=bug.id, run_id=run_id, env_file=args.env_file,
            )
            try:
                if not args.json_output:
                    print(f"\n{_paint('╭─ REPROFY · FIX', '1;36')}  {_paint(bug.id, '1')}")
                    for step, details in backends.models.items():
                        print(f"  {_paint(f'{step.upper():<12}', '36')} {details['backend']}  ·  {details['model']}", flush=True)
                    print(_paint("╰─ ANALYSIS", '1;36'), flush=True)
                result = fix_bug(
                    args.project, bug.id, backends, config=config, run_id=run_id,
                    on_event=_progress,
                )
            finally:
                backends.close()
            if args.json_output:
                print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
            else:
                outcome_color = "1;32" if result.status == "fix_verified" else "1;31"
                print(f"\n{_paint('╭─ RESULT', '1;36')}")
                print(f"│  {_paint('OUTCOME', '1')}  {_paint(result.status.upper(), outcome_color)}")
                print(f"│  {_paint('SAVED AT', '1')}  {result.artifact_dir}")
                print(_paint("╰─", '1;36'))
                if result.diff:
                    print(f"\n{_paint('╭─ PROPOSED DIFF', '1;35')}")
                    _print_diff(result.diff)
                    print(_paint("╰─ /PROPOSED DIFF", '1;35'))
                    print(f"\n{_paint('✓ VERIFIED', '1;32')}  Generated test and project suite passed")
                    print(_paint('REVIEW', '1;33') + "  Check that the test matches the report and the patch changes the right behavior.")
                    print(f"\n{_paint('NEXT', '1;36')}  Preview before approval:")
                    print(f"  reprofy apply {bug.id} --project {shlex.quote(str(args.project))} --check")
                if result.error:
                    print(f"\n{_paint('ERROR', '1;31')}  {result.error}")
            return 0 if result.status == "fix_verified" else 1
        if args.command == "config":
            config_text = dump_config(load_config(args.project))
            print(f"\n{_paint('╭─ PROJECT CONFIGURATION', '1;36')}\n{config_text.rstrip()}")
            print(_paint("╰─ CONFIGURATION VALID", '1;32'))
            print("Environment values and credentials were not loaded.")
            return 0
        if args.command == "delete":
            if not sys.stdin.isatty():
                raise StoreError("Interactive confirmation is required to delete a bug record")
            store = YamlBugStore(args.project)
            bug = store.get(args.bug_id)
            if bug.status != "applied":
                raise StoreError("Only applied bug reports can be deleted")
            print(f"\n{_paint('╭─ DELETE RESOLVED REPORT', '1;31')}  {_paint(bug.id, '1')}")
            print(f"│ {bug.report.text}")
            print("│ This removes its report record; applied source code and regression tests stay in place.")
            print(_paint("╰─ This cannot be undone.", '31'))
            confirmation = input(f"Type DELETE {bug.id} to confirm: ").strip()
            if confirmation != f"DELETE {bug.id}":
                print(f"\n{_paint('Deletion cancelled.', '33')} No project files changed.")
                return 0
            store.delete_resolved(bug.id)
            print(f"\n{_paint('✓ REPORT DELETED', '1;32')}  {bug.id}")
            return 0
        if args.command in {"report", "list", "show"}:
            load_config(args.project)
            store = YamlBugStore(args.project)
            if args.command == "report":
                bug = store.create(BugReport(
                    text=args.text, expected=args.expected, actual=args.actual, context=args.context,
                ))
                if args.json_output:
                    print(bug.model_dump_json(indent=2, exclude_none=True))
                else:
                    print(f"\n{_paint('╭─ REPORT SAVED', '1;32')}")
                    print(f"│  {_paint(bug.id, '1;36')}  ·  {bug.status}")
                    print(f"│  {bug.report.text}")
                    print(_paint(f"╰─ .reprofy/bugs/{bug.id}.yaml · no model call made", '32'))
            elif args.command == "show":
                bug = store.get(args.bug_id)
                if args.json_output:
                    print(bug.model_dump_json(indent=2, exclude_none=True))
                else:
                    print(f"\n{_paint('╭─ BUG REPORT', '1;36')}  {_paint(bug.id, '1')}")
                    print(_paint(f"│ STATUS  {bug.status}", '36'))
                    print(dump_bug(bug), end="")
                    print(_paint("╰─ END REPORT", '1;36'))
                    _print_bug_usage(args.project, bug.id)
            else:
                bugs = store.list()
                if args.status:
                    bugs = [bug for bug in bugs if bug.status == args.status]
                if args.json_output:
                    print(json.dumps([bug.model_dump(mode="json", exclude_none=True) for bug in bugs], indent=2, ensure_ascii=False))
                elif not bugs:
                    print(f"\n{_paint('No reports yet.', '2')} Submit one with the menu or `reprofy report`.")
                else:
                    print(f"\n{_paint('╭─ BUG REPORTS', '1;36')}  {len(bugs)} total")
                    table_header = f"{'ID':<14} {'STATUS':<16} REPORT"
                    print(f"  {_paint(table_header, '1')}")
                    for bug in bugs:
                        title = " ".join(bug.report.text.split())[:100]
                        status_color = '32' if bug.status == 'applied' else ('33' if bug.status in {'new', 'fix_verified'} else '36')
                        print(f"  {_paint(f'{bug.id:<14}', '1;36')} {_paint(f'{bug.status:<16}', status_color)} {title}")
                    print(_paint("╰─", '1;36'))
            return 0
        requested_bug = str(args.proposal) if re.fullmatch(r"BUG-[0-9]{3,}", str(args.proposal)) else None
        proposal_file = args.proposal
        if requested_bug:
            bug = YamlBugStore(args.project).get(requested_bug)
            if not bug.fix or not bug.fix.proposal_file:
                raise ApplyError("Bug has no verified proposal; run fix first")
            proposal_file = project_path(args.project, bug.fix.proposal_file)
        proposal = load_apply_proposal(proposal_file)
        if requested_bug and proposal.bug_id != requested_bug:
            raise ApplyError("Saved proposal belongs to a different bug")
        diff = prepare_application(args.project, proposal)
        print(f"\n{_paint('╭─ PATCH REVIEW', '1;35')}  {_paint(proposal.bug_id, '1')}")
        print(f"│ {_paint('REPORT', '1;36')}\n│   {proposal.report}")
        print(f"│\n│ {_paint('EXPECTED', '1;36')}\n│   {proposal.expected_behavior}")
        print(f"│\n│ {_paint('WHY THIS CHANGE', '1;36')}\n│   {proposal.explanation}")
        print(f"╰─ {_paint('SOURCE + REGRESSION TEST', '1;35')}\n")
        _print_diff(diff)
        if args.check:
            verify_application(args.project, proposal)
            print(f"\n{_paint('╭─ PREVIEW PASSED', '1;32')}")
            print("│ The patch and regression suite passed fresh verification.")
            print("╰─ The project files are unchanged.")
            return 0
        if not sys.stdin.isatty():
            raise ApplyError("Interactive approval is required. Use --check for noninteractive preview.")

        def approve(reviewed_diff: str) -> bool:
            print(f"\n{_paint('╭─ APPROVAL REQUIRED', '1;33')}")
            print("│ The patch passed fresh verification. Review the source and regression test above.")
            print("╰─ Type APPLY to write these exact changes; press Enter to leave the project unchanged.")
            return input(f"{_paint('Your choice', '1;33')} [APPLY/Enter]: ").strip() == "APPLY"

        result = apply_proposal(args.project, proposal, approve=approve)
        print(f"\n{_paint('╭─ APPLICATION', '1;32' if result.status == 'applied' else '1;33')}")
        print(f"│  {_paint('STATUS', '1')}  {result.status.upper()}")
        for path in result.changed_files:
            print(f"│  {_paint('UPDATED', '32')}  {path}")
        print(_paint("╰─", '1;32'))
        post_apply_passed = True
        if result.status == "applied":
            config = load_config(args.project)
            post_run = run_applied_suite(args.project.resolve(), config)
            if test_run_passed(post_run):
                print(f"\n{_paint('╭─ POST-APPLY CHECK PASSED', '1;32')}")
                print("│ The configured project suite passed after the files were updated.")
                print(_paint("╰─", '1;32'))
            else:
                post_apply_passed = False
                print(f"\n{_paint('╭─ POST-APPLY CHECK FAILED', '1;31')}")
                print("│ The approved patch remains applied. Test output follows:")
                print(post_run.output)
                print(_paint("╰─ Requesting model analysis; no automatic code changes will be made.", '1;33'))
                if proposal.bug_id:
                    analysis_run_id = uuid4().hex
                    backends = None
                    try:
                        backends = build_step_backends(
                            config, args.project.resolve(), bug_id=proposal.bug_id,
                            run_id=analysis_run_id, env_file=args.env_file,
                        )
                        backend = LoggedBackend(
                            backends.fix.backend,
                            args.project / ".reprofy/usage.jsonl",
                            metadata={"bug_id": proposal.bug_id, "step": "post_apply_analysis", "run_id": analysis_run_id},
                        )
                        analysis = analyze_post_apply_failure(
                            backend, bug_id=proposal.bug_id, report=proposal.report,
                            expected_behavior=proposal.expected_behavior, diff=diff,
                            test_output=post_run.output,
                        )
                        analysis_record = {
                            "bug_id": proposal.bug_id, "run_id": analysis_run_id,
                            "test_status": "failed", "test_output": post_run.output,
                            "analysis": analysis.model_dump(mode="json"),
                        }
                        artifact_dir = project_path(args.project, f".reprofy/post-apply/{proposal.bug_id}")
                        artifact_dir.mkdir(parents=True, exist_ok=True)
                        (artifact_dir / f"{analysis_run_id}.json").write_text(json.dumps(analysis_record, indent=2, ensure_ascii=False), encoding="utf-8")
                        print(f"\n{_paint('╭─ MODEL ANALYSIS', '1;36')}")
                        print(f"│ Summary: {analysis.summary}")
                        print(f"│ Likely cause: {analysis.likely_cause}")
                        for action in analysis.recommended_actions:
                            print(f"│  · {action}")
                        print(f"│ Confidence: {analysis.confidence:.0%}")
                        print(_paint(f"╰─ Saved at {artifact_dir / f'{analysis_run_id}.json'}", '36'))
                    except Exception as error:
                        print(f"\n{_paint('MODEL ANALYSIS UNAVAILABLE', '1;31')}  {error}", file=sys.stderr)
                    finally:
                        if backends is not None:
                            backends.close()
        if result.bookkeeping_warning:
            print("WARNING:", result.bookkeeping_warning, file=sys.stderr)
        return 0 if post_apply_passed else 1
    except ValidationError as error:
        print(f"reprofy: {error.json(include_input=False, include_url=False)}", file=sys.stderr)
        return 1
    except (ApplyError, ConfigError, StoreError, EngineError, OSError) as error:
        print(f"reprofy: {error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nApproval cancelled; no application requested.", file=sys.stderr)
        return 1
