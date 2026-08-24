"""Optional command-line adapter for the Spotlight orchestration module."""

from __future__ import annotations

import argparse
import json
import sys

from . import (
    OrchestrationError,
    approve,
    decide_ingest,
    decide_report,
    record_attempt,
    request_follow_up,
    resolve,
    seal_gate1,
)
from .contract import ATTEMPT_LIMITS


def add_case_boundary(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--authorized-cases-root", help=argparse.SUPPRESS)
    parser.add_argument("case_dir")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    add_case_boundary(status)
    approval = commands.add_parser("approve")
    approval.add_argument("gate", choices=("methodology", "gate1"))
    approval.add_argument("--approved-by", required=True)
    approval.add_argument("--approved-at", required=True)
    add_case_boundary(approval)
    attempt = commands.add_parser("record-attempt")
    attempt.add_argument("kind", choices=tuple(ATTEMPT_LIMITS))
    attempt.add_argument("--gap", required=True)
    add_case_boundary(attempt)
    follow_up = commands.add_parser("request-follow-up")
    follow_up.add_argument("--instructions", required=True)
    add_case_boundary(follow_up)
    seal = commands.add_parser("seal-gate1")
    add_case_boundary(seal)
    report = commands.add_parser("decide-report")
    report.add_argument("decision", choices=("completed", "declined"))
    add_case_boundary(report)
    ingest = commands.add_parser("decide-ingest")
    ingest.add_argument("decision", choices=("requested", "completed", "declined"))
    add_case_boundary(ingest)
    return parser


def dispatch(args: argparse.Namespace) -> dict[str, object] | None:
    boundary = {"authorized_cases_root": args.authorized_cases_root}
    if args.command == "status":
        return resolve(args.case_dir, **boundary)
    if args.command == "approve":
        approve(
            args.case_dir,
            args.gate,
            args.approved_by,
            args.approved_at,
            **boundary,
        )
    elif args.command == "record-attempt":
        record_attempt(args.case_dir, args.kind, args.gap, **boundary)
    elif args.command == "request-follow-up":
        request_follow_up(args.case_dir, args.instructions, **boundary)
    elif args.command == "seal-gate1":
        seal_gate1(args.case_dir, **boundary)
    elif args.command == "decide-report":
        decide_report(args.case_dir, args.decision, **boundary)
    else:
        decide_ingest(args.case_dir, args.decision, **boundary)
    return None


def main() -> int:
    args = build_parser().parse_args()
    try:
        value = dispatch(args)
        if value is not None:
            print(
                json.dumps(value, sort_keys=True)
                if args.json
                else f"{value['status']}: {value['phase']}"
            )
    except OrchestrationError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 3
    return 0
