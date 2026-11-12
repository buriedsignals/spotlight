"""The journalist's two intake decisions, backed by the review nonce ledger.

Confirming a material's record and approving its kept claims each count only
when the Intake page spent a one-time nonce for that material (the ledger in
data/review-nonces.json, gates "intake_confirm" and "intake_approve") on a
digest of exactly what was decided. The record or the approval stores the
receipt, and anything that no longer matches its digest is not a decision.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract import OrchestrationError
from .review import INTAKE_GATES, _canonical_sha, decision_backed, load_ledger, nonce_sha256, record_decision
from .storage import case_path

REGISTER = "data/journalist-input.json"
CLAIMS = "data/intake-claims.json"
# What a record's confirmation adds. A derivative is a text version added
# later, covered at approval by the anchor hash of each passage taken from it.
CONFIRMATION_FIELDS = ("state", "confirmed_by", "confirmed_at", "review", "dependency_digest", "derivatives")


def confirm_digest(record: dict[str, Any]) -> str:
    """The record's fields, which include the registered hash of its file."""
    return _canonical_sha({name: value for name, value in record.items() if name not in CONFIRMATION_FIELDS})


def approve_digest(record: dict[str, Any], claims_doc: dict[str, Any]) -> str:
    """The confirmed record, each kept claim of the material and each of their
    passages whole (text, anchor, hash, language, speaker, rights)."""
    passages = {p["id"]: p for p in claims_doc["passages"] if p["input_id"] == record["id"]}
    kept = sorted((c for c in claims_doc["claims"] if c["passage_id"] in passages and c["state"] == "kept"), key=lambda c: c["id"])
    shown = sorted({c["passage_id"] for c in kept})
    return _canonical_sha({
        "record": confirm_digest(record),
        "claims": [{name: c[name] for name in ("id", "passage_id", "proposition", "statement")} for c in kept],
        "passages": [{name: value for name, value in passages[i].items() if name != "provenance"} for i in shown],
    })


def subject_digest(action: str, record: dict[str, Any], claims_doc: dict[str, Any]) -> str:
    return confirm_digest(record) if action == "confirm" else approve_digest(record, claims_doc)


def spend(ledger: dict[str, Any], action: str, material_id: str, nonce: str, digest: str, current: str) -> dict[str, Any]:
    """Spend the page's nonce for this decision on this material and the digest
    the page showed. The caller publishes the ledger with the decision, so the
    nonce is spent when the decision is; the receipt it returns is stored with it."""
    hashed = nonce_sha256(nonce)
    entry = ledger["nonces"].get(hashed)
    if not (isinstance(entry, dict) and entry.get("channel") == "page" and entry.get("gate") == INTAKE_GATES[action]
            and entry.get("subject") == material_id):
        raise OrchestrationError(f"the code was not issued to {action} {material_id} on the Intake page")
    if entry.get("used") is not False:
        raise OrchestrationError("the code has already been used")
    if digest != current:
        raise OrchestrationError(f"{material_id} changed since the page showed it; reload the page and decide again")
    entry["used"] = True
    record_decision(ledger, {"nonce_sha256": hashed}, "approved", current)
    return {"review": {"channel": "page", "nonce_sha256": hashed}, "dependency_digest": current}


def _backed(case: Path, action: str, material_id: str, receipt: object, current: str, ledger: dict[str, Any]) -> bool:
    """The receipt cites a nonce the page spent for this decision on this
    material, for exactly the content as it is now."""
    if not isinstance(receipt, dict) or receipt.get("dependency_digest") != current:
        return False
    if not decision_backed(case, INTAKE_GATES[action], receipt, ledger, bind_review=False, require_bound=True):
        return False
    entry = ledger["nonces"][receipt["review"]["nonce_sha256"]]
    return entry.get("subject") == material_id and receipt["review"].get("channel") == "page"


def confirmed(case: Path, record: dict[str, Any], ledger: dict[str, Any]) -> bool:
    """A record counts as confirmed only when the page confirmed these fields."""
    return record.get("state") == "confirmed" and _backed(case, "confirm", record["id"], record, confirm_digest(record), ledger)


def counted_approvals(case: Path) -> dict[str, dict[str, Any]]:
    """The approvals in data/intake-claims.json that count: the page spent a
    nonce on them, their record is confirmed the same way, and the record,
    claims and passages are what the approval covered. Anything else, a
    hand-written entry included, is not an approval."""
    register, claims = _read(case, REGISTER), _read(case, CLAIMS)
    if not isinstance(register.get("inputs"), list) or not isinstance(claims.get("approvals"), dict):
        return {}
    ledger = load_ledger(case)
    records = {r["id"]: r for r in register["inputs"] if isinstance(r, dict) and isinstance(r.get("id"), str)}
    return {material_id: approval for material_id, approval in claims["approvals"].items()
            if material_id in records and _counts(case, records[material_id], approval, claims, ledger)}


def _counts(case: Path, record: dict[str, Any], approval: object, claims: dict[str, Any], ledger: dict[str, Any]) -> bool:
    try:
        return confirmed(case, record, ledger) and _backed(case, "approve", record["id"], approval, approve_digest(record, claims), ledger)
    except (KeyError, TypeError):
        return False  # a hand-edited file of the wrong shape holds no decision


def _read(case: Path, relative: str) -> dict[str, Any]:
    try:
        value = json.loads(case_path(case, relative).read_bytes())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
