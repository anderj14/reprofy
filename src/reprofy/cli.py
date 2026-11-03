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
from .config import ConfigError, dump_config, initialize_project, load_config, project_path
from .bugs import BUG_STATUSES, BugReport
from .store import StoreError, YamlBugStore, dump_bug
from .backends import build_step_backends
from .engine import EngineError, fix_bug


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reprofy")
    subcommands = parser.add_subparsers(dest="command", required=True)
    init = subcommands.add_parser("init", help="Create project configuration and local state")
    init.add_argument("--project", type=Path, default=Path.cwd())
    init.add_argument("--source-dir", action="append", dest="source_dirs")
    init.add_argument("--tests-dir", default="tests")
    init.add_argument("--backend", choices=["meta", "baseten"], default="meta")
    config = subcommands.add_parser("config", help="Validate and display project configuration")
    config.add_argument("--project", type=Path, default=Path.cwd())
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
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            initialize_project(
                args.project, source_dirs=args.source_dirs,
                tests_dir=args.tests_dir, backend=args.backend,
            )
            print(f"INITIALIZED: {args.project.resolve()}")
            print("Created reprofy.yaml and .reprofy/bugs/; model names remain environment references.")
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
                    for step, details in backends.models.items():
                        print(f"MODEL {step}: {details['backend']} / {details['model']}", flush=True)
                result = fix_bug(
                    args.project, bug.id, backends, config=config, run_id=run_id,
                    on_event=lambda message: print(message, file=sys.stderr, flush=True),
                )
            finally:
                backends.close()
            if args.json_output:
                print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
            else:
                print(f"OUTCOME: {result.status}")
                print("ARTIFACTS:", result.artifact_dir)
                if result.diff:
                    print("PROPOSED DIFF:\n" + result.diff)
                    print("Tests passed. Review the reproduction's meaning and source changes before approval.")
                    print(f"NEXT: python -m reprofy apply {bug.id} --project {shlex.quote(str(args.project))} --check")
                if result.error:
                    print("ERROR:", result.error)
            return 0 if result.status == "fix_verified" else 1
        if args.command == "config":
            print(dump_config(load_config(args.project)), end="")
            print("CONFIG OK (environment values and credentials were not loaded)")
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
                    print(f"CREATED: {bug.id} [{bug.status}]")
                    print(f"Saved in .reprofy/bugs/{bug.id}.yaml; no model call was made.")
            elif args.command == "show":
                bug = store.get(args.bug_id)
                print(bug.model_dump_json(indent=2, exclude_none=True) if args.json_output else dump_bug(bug), end="\n" if args.json_output else "")
            else:
                bugs = store.list()
                if args.status:
                    bugs = [bug for bug in bugs if bug.status == args.status]
                if args.json_output:
                    print(json.dumps([bug.model_dump(mode="json", exclude_none=True) for bug in bugs], indent=2, ensure_ascii=False))
                elif not bugs:
                    print("No bugs recorded.")
                else:
                    print(f"{'ID':<14} {'STATUS':<16} REPORT")
                    for bug in bugs:
                        title = " ".join(bug.report.text.split())[:100]
                        print(f"{bug.id:<14} {bug.status:<16} {title}")
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
        print("REPORT:", proposal.report)
        print("EXPECTED BEHAVIOR:", proposal.expected_behavior)
        print("EXPLANATION:", proposal.explanation)
        print(diff)
        if args.check:
            verify_application(args.project, proposal)
            print("READY: saved patch and regression suite verify; project unchanged.")
            return 0
        if not sys.stdin.isatty():
            raise ApplyError("Interactive approval is required. Use --check for noninteractive preview.")

        def approve(reviewed_diff: str) -> bool:
            print("The patch passed fresh verification. Review both the source and regression test.")
            return input("Type APPLY to approve this exact diff, or Enter to reject: ").strip() == "APPLY"

        result = apply_proposal(args.project, proposal, approve=approve)
        print(result.status.upper())
        for path in result.changed_files:
            print(path)
        if result.bookkeeping_warning:
            print("WARNING:", result.bookkeeping_warning, file=sys.stderr)
        return 0
    except ValidationError as error:
        print(f"reprofy: {error.json(include_input=False, include_url=False)}", file=sys.stderr)
        return 1
    except (ApplyError, ConfigError, StoreError, EngineError, OSError) as error:
        print(f"reprofy: {error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nApproval cancelled; no application requested.", file=sys.stderr)
        return 1
