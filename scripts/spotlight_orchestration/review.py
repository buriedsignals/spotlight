"""Gate review records, page views and one-time decision nonces.

A gate page (methodology, Investigation gate, report, ingest) reads a view built
here, saves the member's edits to one canonical review file per gate, and posts
its decision with the digest it showed. The local review server holds a nonce
issued here; the transitions accept a page decision only with that recorded,
unused nonce, and the resolver accepts a decision only when the nonce ledger
backs it. Only the nonce's hash is stored in the case.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

from .case_writer import atomic_write_files
from .contract import METHODOLOGY_INPUTS, OrchestrationError, SCRIPT_DIR
from .dependencies import current_hashes, investigation_gate_dependencies
from .storage import LEGACY_CHANNEL, LEGACY_LEDGER_KEY, case_path, read_data_bytes

REVIEW_VERSION = "spotlight-review/v1"
LEDGER_VERSION = "spotlight-review-nonces/v1"
LEDGER_FILE = "review-nonces.json"
REVIEW_GATES = ("methodology", "investigation_gate", "report", "ingest")
DECISIONS = {
    "methodology": ("approved",),
    "investigation_gate": ("approved",),
    "report": ("completed", "declined"),
    "ingest": ("requested", "declined"),
}
DISPOSITIONS = ("keep", "set_aside")
# Batch 1.1 flag dispositions, decided on the ingest page.
FLAG_ACTIONS = ("amend", "exclude", "investigate_further", "accept_with_reason")
INGEST_BATCH = "data/knowledge-batch.json"
INGEST_DRAFT = "data/knowledge-batch-draft.json"
INGEST_PROPOSAL = "data/knowledge-proposal.json"
MAX_NOTE = 2000
MAX_REVIEWER = 200
_POSITIVE_VERDICTS = frozenset({"verified", "partially_verified"})


class ReviewDigestChanged(OrchestrationError):
    """The record changed after the page loaded it; the page must reload."""


def review_file(gate: str) -> str:
    return f"{gate.replace('_', '-')}-review.json"


def require_gate(gate: str) -> None:
    if gate not in REVIEW_GATES:
        raise OrchestrationError(f"unknown review gate: {gate}")


def pending_gate(status: dict[str, Any]) -> str | None:
    """The gate a member can decide now, from a resolver status."""
    phase = status.get("next_phase")
    if phase == "methodology_approval":
        return "methodology"
    if phase == "investigation_gate_approval":
        return "investigation_gate"
    if phase == "report":
        return "report"
    if phase == "ingest" and status.get("ingest", {}).get("resume_at") == "decision":
        return "ingest"
    return None


def _canonical_sha(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def nonce_sha256(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def _load_json_data(
    case: Path, name: str, data_descriptor: int | None
) -> dict[str, Any] | None:
    content = read_data_bytes(case, name, data_descriptor)
    if content is None:
        return None
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"cannot read data/{name}: {exc}") from exc
    if not isinstance(value, dict):
        raise OrchestrationError(f"data/{name} must be an object")
    return value


def review_sha256(case: Path, gate: str, data_descriptor: int | None = None) -> str | None:
    content = read_data_bytes(case, review_file(gate), data_descriptor)
    return None if content is None else hashlib.sha256(content).hexdigest()


def load_ledger(case: Path, data_descriptor: int | None = None) -> dict[str, Any]:
    ledger = _load_json_data(case, LEDGER_FILE, data_descriptor)
    if ledger is None:
        return {"schema_version": LEDGER_VERSION, "nonces": {}}
    if ledger.get("schema_version") != LEDGER_VERSION or not isinstance(
        ledger.get("nonces"), dict
    ):
        raise OrchestrationError(f"data/{LEDGER_FILE} has an unsupported schema")
    return ledger


def effective_ledger(
    case: Path, state: dict[str, Any], data_descriptor: int | None = None
) -> dict[str, Any]:
    """The ledger file plus, for a state just upgraded from v1, its in-memory
    legacy entries (published by the first v2 write)."""
    ledger = load_ledger(case, data_descriptor)
    legacy = state.get(LEGACY_LEDGER_KEY)
    if isinstance(legacy, dict) and legacy:
        ledger = {**ledger, "nonces": {**ledger["nonces"], **legacy}}
    return ledger


def ledger_bytes(ledger: dict[str, Any]) -> bytes:
    return (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode("utf-8")


def subject_digest(case: Path, gate: str) -> str:
    """What the decision is about: the methodology inputs, or the gate
    dependencies. For ingest it also covers the final knowledge batch, so a
    requested ingestion binds exactly the batch the page showed (R6)."""
    if gate == "methodology":
        return _canonical_sha(current_hashes(case, METHODOLOGY_INPUTS))
    dependency = str(investigation_gate_dependencies(case)["dependency_digest"])
    if gate == "ingest" and activated_case(case):
        return _canonical_sha({"dependencies": dependency, "batch": _batch_sha256(case)})
    return dependency


def _batch_sha256(case: Path) -> str | None:
    path = case_path(case, INGEST_BATCH)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def activated_case(case: Path) -> bool:
    findings = _read_case_json(case, "data/findings.json")
    return isinstance(findings, dict) and findings.get("schema_version") == "1.1" and case_path(case, "data/case-contract.json").is_file()


def ingest_mode() -> str:
    """knowledge_destination.mode from the install config Engine writes:
    "solo" (the default) or "newsroom"."""
    try:
        config = json.loads((SCRIPT_DIR.parent / ".spotlight-config.json").read_text(encoding="utf-8"))
        mode = config.get("knowledge_destination", {}).get("mode", "solo")
    except (OSError, ValueError, AttributeError):
        return "solo"
    return mode if mode in ("solo", "newsroom") else "solo"


def rebuild_ingest_batch(case: Path) -> None:
    """Rebuild the draft and, when nothing is pending, the final batch, after
    a page edit. Runs outside the edit's lock: the builder takes it itself.
    A refusal is recorded in the draft, which the page shows as blocking."""
    if not activated_case(case) or ingest_mode() != "solo" or not case_path(case, INGEST_PROPOSAL).is_file():
        return
    done = subprocess.run([sys.executable, str(SCRIPT_DIR / "build-knowledge-batch.py"), str(case), "--review"],
                          capture_output=True, text=True, check=False)
    if done.returncode not in (0, 2):
        # Not a recorded refusal: the builder failed outright. The edit has
        # already removed the final batch, so the page blocks; say why.
        raise OrchestrationError(f"the knowledge batch rebuild failed: {(done.stderr or done.stdout).strip()[-500:]}")


def review_digest(case: Path, gate: str, data_descriptor: int | None = None) -> str:
    """The digest a page shows and a receipt binds: subject plus saved review."""
    require_gate(gate)
    return _canonical_sha(
        {
            "gate": gate,
            "subject": subject_digest(case, gate),
            "review_sha256": review_sha256(case, gate, data_descriptor),
        }
    )


def issue_nonce(case: Path, data_descriptor: int, gate: str) -> str:
    """Record a fresh page nonce for one gate and return it to the caller only."""
    require_gate(gate)
    ledger = load_ledger(case, data_descriptor)
    # Hex, so a nonce never starts with "-" and passes safely as a CLI argument.
    nonce = secrets.token_hex(32)
    ledger["nonces"][nonce_sha256(nonce)] = {"gate": gate, "channel": "page", "used": False}
    atomic_write_files(
        case, {f"data/{LEDGER_FILE}": ledger_bytes(ledger)}, data_descriptor=data_descriptor
    )
    return nonce


def consume_nonce(
    case: Path,
    data_descriptor: int,
    gate: str,
    nonce: str | None,
    digest: str | None,
    *,
    ready_check: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a decision's nonce and digest; return (ledger, receipt binding).

    The caller publishes the returned ledger with its state write so the nonce
    is spent exactly when the decision becomes durable. Without a nonce the
    decision comes from the command-line adapter: it is recorded under its own
    ``direct`` channel so a hand-written state file is still not backed.
    """
    ledger = load_ledger(case, data_descriptor)
    current = review_digest(case, gate, data_descriptor)
    if nonce is None:
        if digest is not None:
            raise OrchestrationError("a review digest requires its review nonce")
        hashed = nonce_sha256(secrets.token_urlsafe(32))
        ledger["nonces"][hashed] = {"gate": gate, "channel": "direct", "used": True}
        channel = "direct"
    else:
        hashed = nonce_sha256(nonce)
        entry = ledger["nonces"].get(hashed)
        if not isinstance(entry, dict) or entry.get("channel") != "page":
            raise OrchestrationError("review nonce is not recorded for this case")
        if entry.get("gate") != gate:
            raise OrchestrationError("review nonce was issued for another gate")
        if entry.get("used") is not False:
            raise OrchestrationError("review nonce has already been used")
        if digest != current:
            raise ReviewDigestChanged(
                "review digest changed since the page loaded; reload and decide again"
            )
        if ready_check:
            view = build_view(case, gate, data_descriptor)
            if view["blocking"] or view["open_required"]:
                raise OrchestrationError(
                    "the review still has blocking failures or open dispositions"
                )
        entry["used"] = True
        channel = "page"
    binding = {
        "channel": channel,
        "nonce_sha256": hashed,
        "review_digest": current,
        "review_sha256": review_sha256(case, gate, data_descriptor),
    }
    return ledger, binding


def decision_backed(
    case: Path,
    gate: str,
    receipt: object,
    ledger: dict[str, Any] | None = None,
    *,
    bind_review: bool = True,
) -> bool:
    """True when a stored decision cites a spent nonce for its gate."""
    if not isinstance(receipt, dict):
        return False
    binding = receipt.get("review")
    if not isinstance(binding, dict):
        return False
    hashed = binding.get("nonce_sha256")
    try:
        entries = (ledger or load_ledger(case))["nonces"]
    except OrchestrationError:
        return False
    entry = entries.get(hashed) if isinstance(hashed, str) else None
    if not (
        isinstance(entry, dict)
        and entry.get("gate") == gate
        and entry.get("used") is True
        and entry.get("channel") == binding.get("channel")
    ):
        return False
    if binding.get("channel") == LEGACY_CHANNEL:
        return True  # nothing was reviewed on a page before the ledger existed
    return not bind_review or binding.get("review_sha256") == review_sha256(case, gate)


# ---------------------------------------------------------------- views


def _run_check(arguments: list[str]) -> list[str]:
    """Run a deterministic validator; return its error lines (empty when it passes)."""
    try:
        result = subprocess.run(
            [sys.executable, *arguments], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        return [f"validator could not run: {exc}"]
    if result.returncode == 0:
        return []
    lines = [
        line.strip().lstrip("•").strip()
        for line in (result.stderr + "\n" + result.stdout).splitlines()
        if line.strip().startswith("•")
    ]
    if lines:
        return lines[:50]
    detail = (result.stdout.strip() or result.stderr.strip() or "validator failed")
    return [line for line in detail.splitlines() if line.strip()][:20]


def _read_case_json(case: Path, relative: str) -> Any:
    path = case_path(case, relative)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _read_case_text(case: Path, relative: str, limit: int = 20_000) -> str | None:
    path = case_path(case, relative)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except (OSError, UnicodeDecodeError):
        return None


def _text(value: object, limit: int = 4000) -> str:
    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    return str(value)[:limit] if value is not None else ""


def _methodology_content(case: Path) -> tuple[dict[str, Any], list[str]]:
    methodology = _read_case_json(case, "data/methodology.json")
    blocking: list[str] = []
    if not isinstance(methodology, dict):
        return {"brief": _read_case_text(case, "brief-directions.txt") or "", "directions": []}, [
            "data/methodology.json is missing or unreadable"
        ]
    # The validator needs the install's config; Preflight writes it at the
    # checkout root. Without it the check cannot run, which is not a failure
    # of the plan, so the page says so instead of blocking.
    config = SCRIPT_DIR.parent / ".spotlight-config.json"
    if config.is_file():
        blocking += _run_check(
            [
                str(SCRIPT_DIR / "validate-methodology-navigator.py"),
                str(case),
                "--config",
                str(config),
            ]
        )
    directions = []
    plan = methodology.get("investigation_plan")
    for direction in plan if isinstance(plan, list) else []:
        if not isinstance(direction, dict):
            continue
        steps = direction.get("steps") if isinstance(direction.get("steps"), list) else []
        directions.append(
            {
                "direction": _text(direction.get("direction")),
                "questions": [_text(q, 1000) for q in direction.get("questions", [])][:30]
                if isinstance(direction.get("questions"), list)
                else [],
                "steps": [
                    {
                        "action": _text(step.get("action"), 1000),
                        "tool": _text(step.get("tool"), 200),
                        "target": _text(step.get("target"), 500),
                    }
                    for step in steps[:40]
                    if isinstance(step, dict)
                ],
            }
        )
    content = {
        "brief": _read_case_text(case, "brief-directions.txt") or "",
        "lead": _text(methodology.get("lead")),
        "directions": directions,
        "limitations": [_text(item, 1000) for item in methodology.get("limitations", [])][:30]
        if isinstance(methodology.get("limitations"), list)
        else [],
    }
    return content, blocking


def _investigation_content(case: Path) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    dependencies = investigation_gate_dependencies(case)
    blocking = [f"missing: {item}" for item in dependencies["investigation_gate_missing"]]
    blocking += _run_check([str(SCRIPT_DIR / "validate-case.py"), str(case)])
    findings_doc = _read_case_json(case, "data/findings.json")
    fact_check = _read_case_json(case, "data/fact-check.json")
    verdicts: dict[str, dict[str, Any]] = {}
    claims = fact_check.get("claims") if isinstance(fact_check, dict) else None
    for claim in claims if isinstance(claims, list) else []:
        if isinstance(claim, dict) and isinstance(claim.get("finding_id"), str):
            verdicts[claim["finding_id"]] = claim
    findings = []
    items = []
    raw = findings_doc.get("findings") if isinstance(findings_doc, dict) else None
    for finding in raw if isinstance(raw, list) else []:
        if not isinstance(finding, dict) or not isinstance(finding.get("id"), str):
            continue
        finding_id = finding["id"]
        claim = verdicts.get(finding_id, {})
        verdict = claim.get("verdict") if isinstance(claim.get("verdict"), str) else None
        sources = finding.get("sources") if isinstance(finding.get("sources"), list) else []
        findings.append(
            {
                "id": finding_id,
                "claim": _text(finding.get("claim")),
                "evidence": _text(finding.get("evidence")),
                "confidence": _text(finding.get("confidence"), 40),
                "verdict": verdict or "not checked",
                "fact_check_notes": _text(claim.get("notes"), 2000),
                "sources": [
                    {
                        "url": _text(source.get("url"), 2000),
                        "description": _text(source.get("description"), 500),
                    }
                    for source in sources[:20]
                    if isinstance(source, dict)
                ],
            }
        )
        target = f"finding:{finding_id}"
        if verdict == "verified":
            items.append(
                {
                    "key": f"selection:{finding_id}",
                    "kind": "selection",
                    "target": target,
                    "suggested": True,
                    "suggestion_source": "fact-check verdict",
                    "suggestion_reason": "The fact-checker verified this finding.",
                    "required": False,
                }
            )
        else:
            # A finding the fact-checker did not verify is a flag: the member
            # decides it, so it opens without a prefilled value.
            items.append(
                {
                    "key": f"disposition:{finding_id}",
                    "kind": "disposition",
                    "target": target,
                    "suggested": None,
                    "suggestion_source": "fact-check verdict",
                    "suggestion_reason": f"Fact-check verdict: {verdict or 'not checked'}.",
                    "required": True,
                }
            )
    summary = _read_case_json(case, "data/summary.json")
    content = {
        "title": _text(summary.get("title"), 500) if isinstance(summary, dict) else "",
        "summary": _text(summary.get("summary")) if isinstance(summary, dict) else "",
        "findings": findings,
    }
    return content, blocking, items


def _report_content(case: Path) -> dict[str, Any]:
    draft = _read_case_json(case, "data/report-draft.json")
    summary = _read_case_json(case, "data/summary.json")
    return {
        "title": _text(summary.get("title"), 500) if isinstance(summary, dict) else "",
        "draft_ready": isinstance(draft, dict),
        "draft_title": _text(draft.get("title"), 500) if isinstance(draft, dict) else "",
    }


def _ingest_content(case: Path) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """The batch preview the builder made, with one selection per proposed
    record and one disposition per fact-check flag. Legacy (1.0) cases keep
    their own ingest flow and show no batch."""
    if not activated_case(case):
        return {"batch": {"state": "empty", "records": []}}, [], []
    if ingest_mode() == "newsroom":
        # Newsroom cases write nothing locally; the package goes to the newsroom.
        return {"batch": {"state": "newsroom", "records": []}}, [], []
    proposal = _read_case_json(case, INGEST_PROPOSAL)
    draft = _read_case_json(case, INGEST_DRAFT)
    final = _read_case_json(case, INGEST_BATCH)
    blocking: list[str] = []
    if not isinstance(proposal, dict):
        blocking.append("the analyst has not proposed a knowledge batch yet")
    elif not isinstance(draft, dict):
        blocking.append("the knowledge batch has not been built yet")
    elif draft.get("error"):
        blocking.append(f"the knowledge batch cannot be built: {_text(draft['error'], 2000)}")
    elif not isinstance(final, dict):
        if draft.get("pending_flags"):
            blocking.append("every flag needs a disposition before the batch can be approved")
        elif draft.get("batch") is None:
            blocking.append("nothing new to ingest: no proposed record is selected, or all are already admitted unchanged")
        else:
            blocking.append("the batch is not rebuilt for the latest edit yet; reload, or ask the agent to rebuild it")
    batch = final if isinstance(final, dict) else (draft or {}).get("batch") if isinstance(draft, dict) else None
    records = []
    for array in ("claims", "events", "story_arcs", "assessments", "source_positions"):
        for record in (batch or {}).get(array, []) if isinstance(batch, dict) else []:
            if isinstance(record, dict):
                records.append({"type": array, "id": _text(record.get("id"), 200), "version": record.get("version"),
                                "text": _text(record.get("proposition") or record.get("title") or record.get("action")
                                              or record.get("verdict") or (record.get("actor") or {}).get("label"), 2000)})
    items = []
    for array, label in (("claims", "claim"), ("events", "event"), ("story_arcs", "story arc")):
        for record in proposal.get(array, []) if isinstance(proposal, dict) and isinstance(proposal.get(array), list) else []:
            if isinstance(record, dict) and isinstance(record.get("key"), str):
                items.append({"key": f"record:{record['key']}", "kind": "selection", "target": f"{label}:{record['key']}",
                              "suggested": True, "suggestion_source": "analyst proposal",
                              "suggestion_reason": f"The analyst proposed this {label}.", "required": False})
    propositions = {c.get("key"): c.get("proposition") for c in proposal.get("claims", []) if isinstance(c, dict)} \
        if isinstance(proposal, dict) and isinstance(proposal.get("claims"), list) else {}
    for flag in (draft or {}).get("flags", []) if isinstance(draft, dict) else []:
        if isinstance(flag, dict) and isinstance(flag.get("key"), str):
            claim = _text(propositions.get(flag.get("claim_key")), 1000)
            items.append({"key": flag["key"], "kind": "flag", "target": f"claim:{flag.get('claim_key')}",
                          "suggested": None, "suggestion_source": "fact-check flag",
                          "suggestion_reason": f"Claim: {claim}. Flag {_text(flag.get('code'), 80)}: {_text(flag.get('reason'), 1000)}",
                          "required": True})
    state = "final" if isinstance(final, dict) else "draft" if isinstance(batch, dict) else "empty"
    content = {"batch": {"state": state, "records": records,
                         "batch_sha256": _batch_sha256(case) if state == "final" else None}}
    return content, blocking, items


def load_review(case: Path, gate: str, data_descriptor: int | None = None) -> dict[str, Any]:
    review = _load_json_data(case, review_file(gate), data_descriptor)
    if review is None:
        return {"schema_version": REVIEW_VERSION, "gate": gate, "reviewer": "", "items": {}}
    if (
        review.get("schema_version") != REVIEW_VERSION
        or review.get("gate") != gate
        or not isinstance(review.get("items"), dict)
    ):
        raise OrchestrationError(f"data/{review_file(gate)} has an unsupported schema")
    return review


def _merge_items(
    suggested: list[dict[str, Any]], saved: dict[str, Any]
) -> list[dict[str, Any]]:
    merged = []
    for item in suggested:
        prior = saved.get(item["key"])
        final = item["suggested"]
        note = ""
        if isinstance(prior, dict):
            final = prior.get("final", final)
            note = prior.get("note", "") if isinstance(prior.get("note"), str) else ""
        merged.append({**item, "final": final, "note": note})
    return merged


def _counts(items: list[dict[str, Any]]) -> dict[str, int]:
    with_suggestion = [item for item in items if item["suggested"] is not None]
    return {
        "items": len(items),
        "accepted_unchanged": sum(1 for i in with_suggestion if i["final"] == i["suggested"]),
        "changed": sum(1 for i in with_suggestion if i["final"] != i["suggested"]),
        "open_required": sum(1 for i in items if i["required"] and i["final"] is None),
    }


def _gate_parts(case: Path, gate: str) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    if gate == "methodology":
        content, blocking = _methodology_content(case)
        return content, blocking, []
    if gate == "investigation_gate":
        return _investigation_content(case)
    if gate == "report":
        return _report_content(case), [], []
    return _ingest_content(case)


def build_view(case: Path, gate: str, data_descriptor: int | None = None) -> dict[str, Any]:
    """The page model: content, blocking failures, flags, selection and digest."""
    require_gate(gate)
    content, blocking, suggested = _gate_parts(case, gate)
    review = load_review(case, gate, data_descriptor)
    items = _merge_items(suggested, review["items"])
    counts = _counts(items)
    return {
        "schema_version": "spotlight-review-view/v1",
        "gate": gate,
        "decisions": list(DECISIONS[gate]),
        "content": content,
        "blocking": blocking,
        "items": items,
        "counts": counts,
        "open_required": counts["open_required"],
        "reviewer": review.get("reviewer", "") if isinstance(review.get("reviewer"), str) else "",
        "digest": review_digest(case, gate, data_descriptor),
    }


def _validated_edit(edit: object, items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(edit, dict):
        raise OrchestrationError("a review edit must be an object")
    kind = edit.get("kind")
    if kind == "reviewer":
        name = edit.get("value")
        if set(edit) != {"kind", "value"} or not isinstance(name, str) or len(name) > MAX_REVIEWER:
            raise OrchestrationError("reviewer must be a short name")
        return edit
    if kind not in {"mark", "note"} or set(edit) != {"kind", "key", "value"}:
        raise OrchestrationError("a review edit has the wrong fields")
    item = items.get(edit["key"]) if isinstance(edit["key"], str) else None
    if item is None:
        raise OrchestrationError("a review edit names an item this gate does not show")
    value = edit["value"]
    if kind == "note":
        if not isinstance(value, str) or len(value) > MAX_NOTE:
            raise OrchestrationError(f"a note must be text of at most {MAX_NOTE} characters")
    elif item["kind"] == "selection":
        if not isinstance(value, bool):
            raise OrchestrationError("a selection mark is true or false")
    elif item["kind"] == "flag":
        if value not in FLAG_ACTIONS:
            raise OrchestrationError(f"a flag disposition is one of: {', '.join(FLAG_ACTIONS)}")
    elif value not in DISPOSITIONS:
        raise OrchestrationError(f"a disposition is one of: {', '.join(DISPOSITIONS)}")
    return edit


def apply_edit(case: Path, data_descriptor: int, gate: str, edit: object) -> None:
    """Apply one page edit in code and save the canonical review file."""
    require_gate(gate)
    _, _, suggested = _gate_parts(case, gate)
    review = load_review(case, gate, data_descriptor)
    current = {item["key"]: item for item in _merge_items(suggested, review["items"])}
    edit = _validated_edit(edit, current)
    if edit["kind"] == "reviewer":
        review["reviewer"] = edit["value"].strip()
    elif edit["kind"] == "note":
        current[edit["key"]]["note"] = edit["value"]
    else:
        current[edit["key"]]["final"] = edit["value"]
    # Items the gate does not show right now (a flag of a deselected claim)
    # keep their saved decision for when they come back.
    stored = {key: value for key, value in review["items"].items() if key not in current}
    for key, item in current.items():
        stored[key] = {
            "kind": item["kind"],
            "target": item["target"],
            "suggested": item["suggested"],
            "suggestion_source": item["suggestion_source"],
            "suggestion_reason": item["suggestion_reason"],
            "final": item["final"],
            "note": item["note"],
        }
    review["items"] = stored
    review["counts"] = _counts(list(current.values()))
    review["subject_digest"] = subject_digest(case, gate)
    content = (json.dumps(review, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_files(
        case, {f"data/{review_file(gate)}": content}, data_descriptor=data_descriptor
    )
    if gate == "ingest" and activated_case(case):
        # The batch no longer reflects the review: no request may bind it
        # until the rebuild that follows this edit writes the new one.
        case_path(case, INGEST_BATCH).unlink(missing_ok=True)
