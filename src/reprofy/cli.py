"""Initial product CLI: approved application of a structured proposal."""

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from .applying import (
    ApplyError, apply_proposal, load_apply_proposal, prepare_application, verify_application,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reprofy")
    subcommands = parser.add_subparsers(dest="command", required=True)
    apply = subcommands.add_parser("apply", help="Review and apply a test-verified proposal")
    apply.add_argument("proposal", type=Path, help="Path to apply.json")
    apply.add_argument("--project", type=Path, default=Path.cwd())
    apply.add_argument("--check", action="store_true", help="Verify and preview without applying")
    args = parser.parse_args(argv)
    try:
        proposal = load_apply_proposal(args.proposal)
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
        return 0
    except (ApplyError, ValidationError, OSError) as error:
        print(f"reprofy: {error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nApproval cancelled; no application requested.", file=sys.stderr)
        return 1
