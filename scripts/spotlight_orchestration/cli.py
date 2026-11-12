"""Optional command-line adapter for the Spotlight orchestration module."""

from __future__ import annotations

import argparse
from pathlib import Path
import json
import sys

from . import (
    OrchestrationError,
    approve,
    continue_without_research,
    resume_stopped_research,
    repair_claim_links,
    decide_ingest,
    decide_report,
    issue_review_nonce,
    record_amendment,
    record_attempt,
    reset_corrections,
    request_follow_up,
    resolve,
    review_edit,
    review_passages,
    review_view,
    seal_investigation_gate,
    start_research_round,
    write_report_skeleton,
    report_quotes,
)
from .contract import RECORDABLE_ATTEMPTS
from . import review
from .dependencies import material_only_case
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
    # Code's count of a failed correction: the resolver keeps reporting the real failures.
    attempt.add_argument("--no-reopen", dest="reopen", action="store_false")
    add_case_boundary(attempt)
    add_case_boundary(commands.add_parser("reset-corrections"))
    add_case_boundary(commands.add_parser("resume-research"))
    add_case_boundary(commands.add_parser("repair-claim-links"))
    research_round = commands.add_parser("start-research-round")
    add_case_boundary(research_round)
    amendment = commands.add_parser("record-amendment")
    amendment.add_argument("--build", action="store_true")
    add_case_boundary(amendment)
    without_research = commands.add_parser("continue-without-research")
    add_case_boundary(without_research)
    follow_up = commands.add_parser("request-follow-up")
    follow_up.add_argument("--instructions", required=True)
    add_review_receipt(follow_up)
    add_decider(follow_up)
    # Without a page nonce the follow-up is the agent's; the harness names
    # the requesting role (default orchestrator), never a member.
    follow_up.add_argument("--requested-by-role")
    follow_up.add_argument("--shown-gate", choices=("report",))
    follow_up.add_argument("--shown-digest")
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
    passages = commands.add_parser("review-passages")
    passages.add_argument("gate", choices=REVIEW_GATES)
    add_case_boundary(passages)
    skeleton = commands.add_parser("report-skeleton")
    add_case_boundary(skeleton)
    edit = commands.add_parser("review-edit")
    edit.add_argument("gate", choices=REVIEW_GATES)
    edit.add_argument("--edit", required=True, help="one edit as a JSON object")
    add_case_boundary(edit)
    return parser


# The newsroom edition's Ingest steps reach the agent here, with the resolved
# state, and only on a newsroom-mode install: the skills stay the solo steps,
# and a solo install's status carries nothing more.
NEWSROOM_INGEST = (
    "Newsroom edition. This replaces what the ingest skill says about newsroom mode. "
    "Draft and review the batch as an activated solo case does: the analyst's proposal, the built batch, the ingest page. "
    "Nothing is written to a member wiki. Once the member has requested ingestion (resume_at \"ingest\"), "
    "submit the approved batch by code: the newsroom_submit tool in the Pi harness, "
    "or in any other runtime `node harness/pi/src/newsroom-submit.ts {CASE_DIR}`. "
    "Never type the batch into a tool call yourself. If the newsroom refuses the batch, "
    "give the member its reasons and stop; the case stays at Ingest. Then resolve again."
)


def _with_newsroom_note(status: dict[str, object], case_dir: str) -> dict[str, object]:
    # A case that holds only material has no analyst step and, in the
    # newsroom edition, nothing to submit: the note would mislead.
    if status.get("next_phase") != "ingest" or material_only_case(Path(case_dir).resolve()):
        return status
    try:
        newsroom = review.ingest_mode() == "newsroom"
    except OrchestrationError:
        return status
    return {**status, "newsroom_edition": NEWSROOM_INGEST} if newsroom else status


def dispatch(args: argparse.Namespace) -> dict[str, object] | None:
    boundary = {"authorized_cases_root": args.authorized_cases_root}
    if args.command == "status":
        return _with_newsroom_note(resolve(args.case_dir, **boundary), args.case_dir)
    if args.command == "review-view":
        return review_view(args.case_dir, args.gate, **boundary)
    if args.command == "review-passages":
        return review_passages(args.case_dir, args.gate, **boundary)
    if args.command == "issue-review-nonce":
        return {"nonce": issue_review_nonce(args.case_dir, args.gate, **boundary)}
    if args.command == "report-skeleton":
        return {"written": write_report_skeleton(args.case_dir, **boundary), "quotes": report_quotes(args.case_dir, **boundary)}
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
        record_attempt(args.case_dir, args.kind, args.gap, args.reopen, **boundary)
    elif args.command == "reset-corrections":
        reset_corrections(args.case_dir, **boundary)
    elif args.command == "start-research-round":
        start_research_round(args.case_dir, **boundary)
    elif args.command == "record-amendment":
        record_amendment(args.case_dir, build=args.build, **boundary)
    elif args.command == "repair-claim-links":
        repair_claim_links(args.case_dir, **boundary)
    elif args.command == "resume-research":
        resume_stopped_research(args.case_dir, **boundary)
    elif args.command == "continue-without-research":
        continue_without_research(args.case_dir, **boundary)
    elif args.command == "request-follow-up":
        request_follow_up(
            args.case_dir,
            args.instructions,
            **receipt,
            **decider,
            requested_by_role=args.requested_by_role,
            shown_gate=args.shown_gate,
            shown_digest=args.shown_digest,
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
