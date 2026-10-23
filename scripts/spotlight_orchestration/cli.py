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
    issue_review_nonce,
    record_amendment,
    record_attempt,
    request_follow_up,
    resolve,
    review_edit,
    review_view,
    seal_investigation_gate,
    start_research_round,
)
from .contract import RECORDABLE_ATTEMPTS
from .review import REVIEW_GATES, ReviewDigestChanged

# Exit code the review server reads as "the record changed; reload the page".
DIGEST_CHANGED_EXIT = 4


def add_review_receipt(parser: argparse.ArgumentParser) -> None:
    # The review server passes the page's nonce and digest; without them the
    # decision is recorded on the command-line channel.
    parser.add_argument("--nonce", help=argparse.SUPPRESS)
    parser.add_argument("--digest", help=argparse.SUPPRESS)


def add_decider(parser: argparse.ArgumentParser) -> None:
    # The member who decided; the review server passes the page's name.
    parser.add_argument("--decided-by")
    parser.add_argument("--decided-at")


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
    approval.add_argument("gate", choices=("methodology", "research", "investigation_gate"))
    approval.add_argument("--approved-by", required=True)
    approval.add_argument("--approved-at", required=True)
    add_review_receipt(approval)
    add_case_boundary(approval)
    attempt = commands.add_parser("record-attempt")
    attempt.add_argument("kind", choices=RECORDABLE_ATTEMPTS)
    attempt.add_argument("--gap", required=True)
    add_case_boundary(attempt)
    research_round = commands.add_parser("start-research-round")
    add_case_boundary(research_round)
    amendment = commands.add_parser("record-amendment")
    add_case_boundary(amendment)
    follow_up = commands.add_parser("request-follow-up")
    follow_up.add_argument("--instructions", required=True)
    add_review_receipt(follow_up)
    add_decider(follow_up)
    # Without a page nonce the follow-up is the agent's; the harness names
    # the requesting role (default orchestrator), never a member.
    follow_up.add_argument("--requested-by-role")
    add_case_boundary(follow_up)
    seal = commands.add_parser("seal-investigation-gate")
    add_case_boundary(seal)
    report = commands.add_parser("decide-report")
    report.add_argument("decision", choices=("completed", "declined"))
    add_review_receipt(report)
    add_decider(report)
    add_case_boundary(report)
    ingest = commands.add_parser("decide-ingest")
    ingest.add_argument("decision", choices=("requested", "completed", "declined"))
    add_review_receipt(ingest)
    add_decider(ingest)
    add_case_boundary(ingest)
    nonce = commands.add_parser("issue-review-nonce")
    nonce.add_argument("gate", choices=REVIEW_GATES)
    add_case_boundary(nonce)
    view = commands.add_parser("review-view")
    view.add_argument("gate", choices=REVIEW_GATES)
    add_case_boundary(view)
    edit = commands.add_parser("review-edit")
    edit.add_argument("gate", choices=REVIEW_GATES)
    edit.add_argument("--edit", required=True, help="one edit as a JSON object")
    add_case_boundary(edit)
    return parser


def dispatch(args: argparse.Namespace) -> dict[str, object] | None:
    boundary = {"authorized_cases_root": args.authorized_cases_root}
    if args.command == "status":
        return resolve(args.case_dir, **boundary)
    if args.command == "review-view":
        return review_view(args.case_dir, args.gate, **boundary)
    if args.command == "issue-review-nonce":
        return {"nonce": issue_review_nonce(args.case_dir, args.gate, **boundary)}
    if args.command == "review-edit":
        try:
            edit = json.loads(args.edit)
        except json.JSONDecodeError as exc:
            raise OrchestrationError(f"--edit must be JSON: {exc}") from exc
        review_edit(args.case_dir, args.gate, edit, **boundary)
        return review_view(args.case_dir, args.gate, **boundary)
    receipt = {
        "nonce": getattr(args, "nonce", None),
        "digest": getattr(args, "digest", None),
    }
    decider = {
        "decided_by": getattr(args, "decided_by", None),
        "decided_at": getattr(args, "decided_at", None),
    }
    if args.command == "approve":
        approve(
            args.case_dir,
            args.gate,
            args.approved_by,
            args.approved_at,
            **receipt,
            **boundary,
        )
    elif args.command == "record-attempt":
        record_attempt(args.case_dir, args.kind, args.gap, **boundary)
    elif args.command == "start-research-round":
        start_research_round(args.case_dir, **boundary)
    elif args.command == "record-amendment":
        record_amendment(args.case_dir, **boundary)
    elif args.command == "request-follow-up":
        request_follow_up(
            args.case_dir,
            args.instructions,
            **receipt,
            **decider,
            requested_by_role=args.requested_by_role,
            **boundary,
        )
    elif args.command == "seal-investigation-gate":
        seal_investigation_gate(args.case_dir, **boundary)
    elif args.command == "decide-report":
        decide_report(args.case_dir, args.decision, **receipt, **decider, **boundary)
    else:
        decide_ingest(args.case_dir, args.decision, **receipt, **decider, **boundary)
    return None


def main() -> int:
    args = build_parser().parse_args()
    try:
        value = dispatch(args)
        if value is not None and args.command != "status":
            print(json.dumps(value, sort_keys=True))
        elif value is not None:
            print(
                json.dumps(value, sort_keys=True)
                if args.json
                else f"{value['status']}: {value['phase']}"
            )
    except ReviewDigestChanged as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return DIGEST_CHANGED_EXIT
    except OrchestrationError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 3
    return 0
