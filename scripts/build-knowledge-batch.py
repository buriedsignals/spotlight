#!/usr/bin/env python3
"""Build the Knowledge Batch 1.1 (core) for an activated case.

  python3 scripts/build-knowledge-batch.py {CASE_DIR} --model investigator=ID --model fact-checker=ID [--draft]

The analyst proposes only what needs judgment, in data/knowledge-proposal.json
(schemas/knowledge-proposal.schema.json): normalized claims per finding,
events, story arcs and their memberships. This script adds the rest from the
case, deterministically:

- evidence from the evidence bundle, for every capture a used passage cites;
- passages from data/source-expressions.json (active, original language);
- claim-passage links from each passage's links to the claim's finding;
- source positions from data/source-positions.json, re-keyed to the claim;
  an attributed claim without one is refused;
- assessments from the fact-check, with the reviewer's flag dispositions.

Ids come from data/knowledge-ids.json, so a rebuild after review edits reuses
them; a new id is random, never derived from content. A record already
admitted at an unchanged content is not resubmitted; a changed one gets the
next version. Admission itself (the reviewed approval) is not done here.

The reviewer's choices come from data/ingest-review.json: an item
"record:<key>" with final false leaves that proposal record out, and an item
"flag:<claim key>:<code>:<reason hash>" (as listed in the draft) holds the
disposition of that fact-check flag: amend, exclude, investigate_further or
accept_with_reason, the reason in its note. A changed flag needs a new
disposition.

--draft writes data/knowledge-batch-draft.json: the batch without the flags
still awaiting a disposition (null when nothing is new yet), and those flags
listed apart for the review page; it removes any earlier final batch. Without
--draft, any pending flag refuses the build, and the batch goes
to data/knowledge-batch.json. The proposal must match its schema, the case
must validate (bindings included), and the output must pass the core schema
and the cross-record rules, or nothing is written.

Exit codes: 0 = written; 2 = refused (the reason is on stderr).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import schema_check  # noqa: E402
from spotlight_orchestration.case_writer import atomic_write_files  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.storage import transaction  # noqa: E402
from verdicts import FLAG_CODES, VERDICTS_1_1  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("validate_knowledge_batch", SCRIPT_DIR / "validate-knowledge-batch.py")
RULES = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(RULES)

PROPOSAL = "data/knowledge-proposal.json"
REVIEW = "data/ingest-review.json"
IDS = "data/knowledge-ids.json"
BATCH = "data/knowledge-batch.json"
DRAFT = "data/knowledge-batch-draft.json"
INTERFACE_VERSION = "1"
LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
ACTIONS = ("amend", "exclude", "investigate_further", "accept_with_reason")
# Capture route by acquisition method; a browser-driven fetch is "browser".
ROUTES = {
    "dev_browser": "browser", "browse": "browser", "browser_harness": "browser", "browser_use": "browser",
    "crawl4ai": "browser", "firecrawl": "api", "api": "api", "manual": "manual",
}
FETCHED = ("full_text", "open_access", "archive_copy")
# Id prefix per record type: the prefix names the type.
PREFIX = {
    "evidence": "evidence", "source_expression": "passage", "claim": "claim",
    "claim_source_expression": "grounding", "source_position": "position", "assessment": "assessment",
    "event": "event", "claim_event_membership": "membership", "story_arc": "arc",
    "event_story_arc_membership": "arc-membership",
}
ARRAY = {record_type: array for array, record_type in RULES.ARRAYS.items()}
SCHEMAS = SCRIPT_DIR.parent / "schemas"


class BuildError(Exception):
    """The case cannot yield a valid batch; nothing is written."""


def _load(case: Path, relative: str, required: bool = True) -> Any:
    path = case / relative
    if not path.is_file():
        if required:
            raise BuildError(f"{relative} is missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BuildError(f"cannot read {relative}: {exc}") from exc


def _content_sha(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k not in ("id", "version")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_proposal(proposal: Any) -> None:
    """The proposal is model output: it must match its schema exactly, and
    every key it refers to must be one it defines."""
    errors = schema_check.load(SCHEMAS / "knowledge-proposal.schema.json").errors(proposal)
    if errors:
        raise BuildError(f"{PROPOSAL} does not match interface {INTERFACE_VERSION}: " + "; ".join(errors[:6]))
    keys: set[str] = set()
    for array in ("claims", "events", "story_arcs"):
        for item in proposal.get(array, []):
            if item["key"] in keys:
                raise BuildError(f"{PROPOSAL}: key {item['key']!r} is used twice")
            keys.add(item["key"])
    claims = {c["key"] for c in proposal["claims"]}
    events = {e["key"] for e in proposal.get("events", [])}
    for event in proposal.get("events", []):
        if set(event["claim_keys"]) - claims:
            raise BuildError(f"{PROPOSAL}: event {event['key']} names unknown claims {sorted(set(event['claim_keys']) - claims)}")
    for arc in proposal.get("story_arcs", []):
        if set(arc["event_keys"]) - events:
            raise BuildError(f"{PROPOSAL}: story arc {arc['key']} names unknown events {sorted(set(arc['event_keys']) - events)}")


def flag_key(claim_key: str, flag: dict[str, Any]) -> str:
    """A disposition's review key, bound to the flag's code and reason, so a
    reordered or changed flag is never decided by an old disposition."""
    digest = hashlib.sha256(flag["reason"].encode("utf-8")).hexdigest()[:12]
    return f"flag:{claim_key}:{flag['code']}:{digest}"


def _review_items(review: Any) -> dict[str, Any]:
    if review is None:
        return {}
    if not isinstance(review, dict) or review.get("gate") != "ingest" or not isinstance(review.get("items"), dict):
        raise BuildError(f"{REVIEW} is not an ingest review record")
    return review["items"]


class Ids:
    """data/knowledge-ids.json: proposal and case keys to record ids, with
    the record each id was last built and last admitted as (admission, by
    the reviewed approval, moves "pending" to "admitted")."""

    def __init__(self, doc: Any):
        if doc is None:
            doc = {"schema_version": "1.0", "records": {}}
        if not isinstance(doc, dict) or doc.get("schema_version") != "1.0" or not isinstance(doc.get("records"), dict):
            raise BuildError(f"{IDS} has an unsupported shape")
        self.doc = doc
        self.placed: set[str] = set()

    def clear_unplaced(self) -> None:
        """A record left out of this build is no longer pending admission."""
        for name, entry in self.doc["records"].items():
            if name not in self.placed:
                entry.pop("pending", None)

    def place(self, record_type: str, key: str, record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Give a record its id and version; True when it must be submitted."""
        name = f"{record_type}:{key}"
        self.placed.add(name)
        entry = self.doc["records"].setdefault(name, {
            "id": f"{PREFIX[record_type]}:{secrets.token_hex(6)}", "type": record_type})
        if entry.get("type") != record_type or not isinstance(entry.get("id"), str) \
                or not entry["id"].startswith(PREFIX[record_type] + ":"):
            raise BuildError(f"{IDS}: entry {name!r} has the wrong type or id")
        sha = _content_sha(record)
        admitted = entry.get("admitted")
        placed = {"id": entry["id"], **record}
        if isinstance(admitted, dict) and admitted.get("sha256") == sha:
            placed["version"] = admitted["version"]
            entry.pop("pending", None)
            return placed, False
        placed["version"] = admitted["version"] + 1 if isinstance(admitted, dict) else 1
        entry["pending"] = {"version": placed["version"], "sha256": sha, "record": placed}
        return placed, True


def build(case: Path, models: dict[str, str], draft: bool) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    findings = _load(case, "data/findings.json")
    if not isinstance(findings, dict) or findings.get("schema_version") != "1.1" or not (case / "data" / "case-contract.json").is_file():
        raise BuildError("only an activated case (findings contract 1.1) builds a Batch 1.1")
    if not _text(findings.get("project")):
        raise BuildError("findings.json has no project")
    # The case must validate, bindings included: a batch is never built from
    # artifacts that changed after the gate approval covered them.
    checked = subprocess.run([sys.executable, str(SCRIPT_DIR / "validate-case.py"), str(case)], capture_output=True, text=True)
    if checked.returncode != 0:
        raise BuildError("the case does not validate: " + (checked.stderr or checked.stdout).strip()[-600:])
    fact_check = _load(case, "data/fact-check.json")
    if not isinstance(fact_check, dict) or fact_check.get("schema_version") != "1.1":
        raise BuildError("the fact-check must use contract 1.1")
    expressions_doc = _load(case, "data/source-expressions.json")
    bundle = _load(case, "data/evidence-bundle.json")
    positions_doc = _load(case, "data/source-positions.json", required=False) or {"positions": []}
    proposal = _load(case, PROPOSAL)
    check_proposal(proposal)
    review = _review_items(_load(case, REVIEW, required=False))
    ids = Ids(_load(case, IDS, required=False))

    def provenance(actor: str) -> dict[str, Any]:
        if actor == "human":
            return {"method": "journalist"}
        model = models.get(actor)
        if not _text(model):
            raise BuildError(f"no model given for {actor}; pass --model {actor}=ID")
        return {"method": "agent_candidate", "model": model}

    analyst = {"method": "agent_candidate", "model": proposal["model"]}
    selected = lambda key: not (isinstance(review.get(f"record:{key}"), dict) and review[f"record:{key}"].get("final") is False)  # noqa: E731
    finding_ids = {f.get("id") for f in findings.get("findings", []) if isinstance(f, dict)}
    expressions = {e["id"]: e for e in expressions_doc.get("expressions", []) if isinstance(e, dict) and isinstance(e.get("id"), str)}
    items = {i["id"]: i for i in bundle.get("items", []) if isinstance(i, dict) and isinstance(i.get("id"), str)}
    rows = [r for r in fact_check.get("claims", []) if isinstance(r, dict)]

    batch: dict[str, Any] = {"schema_version": "1.1", "batch_id": f"batch:{secrets.token_hex(6)}",
                             "case_id": f"case:{findings.get('project')}"}
    refs: dict[tuple[str, str], dict[str, Any]] = {}

    def emit(record_type: str, key: str, record: dict[str, Any]) -> dict[str, Any]:
        if (record_type, key) in refs:
            return refs[(record_type, key)]
        placed, submit = ids.place(record_type, key, record)
        if submit:
            batch.setdefault(ARRAY[record_type], []).append(placed)
        refs[(record_type, key)] = {"id": placed["id"], "version": placed["version"]}
        return refs[(record_type, key)]

    def active(expression: dict[str, Any]) -> bool:
        events = expression.get("lifecycle_events")
        return isinstance(events, list) and bool(events) and isinstance(events[-1], dict) and events[-1].get("event") == "activated"

    def evidence_ref(item_id: str) -> dict[str, Any]:
        item = items.get(item_id)
        if item is None or not isinstance(item.get("sha256"), str):
            raise BuildError(f"evidence bundle item {item_id!r} is missing or has no original hash")
        content = item.get("raw_path") or item.get("downloaded_document_path") or item.get("screenshot_path")
        if not _text(content) or not _text(item.get("source_url")) or not _text(item.get("accessed")):
            raise BuildError(f"evidence bundle item {item_id!r} needs a stored artifact, source_url and accessed time")
        record = {
            "capture": {"route": ROUTES.get(item.get("acquisition_method"), "other"), "tool": item.get("acquisition_method", "other"),
                        "source_url": item["source_url"], "captured_at": item["accessed"]},
            "content_ref": content,
            "content_sha256": item["sha256"].lower(),
            "provenance": provenance("investigator"),
        }
        derivatives = [{"id": d["id"], "type": d["derivative_type"], "content_ref": d["path"], "content_sha256": d["sha256"].lower()}
                       for d in item.get("text_derivatives") or [] if isinstance(d, dict)]
        if derivatives:
            record["derivatives"] = derivatives
        return emit("evidence", item_id, record)

    def passage_ref(expression_id: str) -> dict[str, Any]:
        expression = expressions.get(expression_id)
        if expression is None or not active(expression):
            raise BuildError(f"passage {expression_id!r} is missing or not active")
        if expression.get("derived_from_expression_id") or expression.get("derivative_type") == "translation":
            raise BuildError(f"passage {expression_id!r} is derived; claims and positions use original-language passages")
        if not isinstance(expression.get("language"), str) or not LANGUAGE.match(expression["language"]):
            raise BuildError(f"passage {expression_id!r} has no BCP 47 language")
        item_id = str(expression.get("original_evidence_bundle_id"))
        origin = {"type": "evidence", **evidence_ref(item_id)}
        anchor = (expression.get("anchor_ref") or {}).get("path")
        derivative = next((d for d in items[item_id].get("text_derivatives") or [] if isinstance(d, dict) and d.get("path") == anchor), None)
        if derivative is not None:
            origin["derivative_id"] = derivative["id"]
        record = {"text": expression["text"], "language": expression["language"],
                  "direct_quote": expression.get("direct_quote") is True, "origin_ref": origin,
                  "provenance": provenance(expression.get("created_by", "investigator"))}
        if _text(expression.get("attribution")):
            record["speaker"] = expression["attribution"]
        return emit("source_expression", expression_id, record)

    pending: list[dict[str, Any]] = []
    claim_refs: dict[str, dict[str, Any]] = {}
    for claim in proposal["claims"]:
        if not selected(claim["key"]):
            continue
        finding_id = claim["finding_id"]
        if finding_id not in finding_ids:
            raise BuildError(f"claim {claim['key']}: finding {finding_id!r} does not exist")
        linked = [e for e in expressions.values() if active(e) and not e.get("derived_from_expression_id")
                  and any(isinstance(l, dict) and l.get("finding_id") == finding_id for l in e.get("finding_links") or [])]
        if claim["origin_expression_id"] not in {e["id"] for e in linked}:
            raise BuildError(f"claim {claim['key']}: origin passage {claim['origin_expression_id']!r} is not an active original passage of {finding_id}")
        statement = copy.deepcopy(claim["statement"])
        ref = emit("claim", claim["key"], {
            "origin": {"finding_id": finding_id}, "proposition": claim["proposition"],
            "origin_source_expression_ref": passage_ref(claim["origin_expression_id"]),
            "language": claim["language"], "statement": statement, "provenance": analyst})
        claim_refs[claim["key"]] = ref
        groundings = []
        for expression in sorted(linked, key=lambda e: e["id"]):
            relation = next(l["relation"] for l in expression["finding_links"] if l.get("finding_id") == finding_id)
            groundings.append({"type": "claim_source_expression", **emit("claim_source_expression", f"{claim['key']}:{expression['id']}", {
                "claim_ref": ref, "source_expression_ref": passage_ref(expression["id"]),
                "role": relation, "provenance": provenance(expression.get("created_by", "investigator"))})})
        positions = []
        for position in positions_doc.get("positions", []):
            if not isinstance(position, dict) or position.get("finding_id") != finding_id or "withdrawn" in position:
                continue
            cited = [i for i in position.get("source_expression_ids", []) if i in expressions and active(expressions[i])
                     and not expressions[i].get("derived_from_expression_id") and expressions[i].get("derivative_type") != "translation"]
            if not cited:
                raise BuildError(f"position {position.get('id')!r} cites no active original-language passage")
            record = {"claim_ref": ref, "actor": {"label": position["actor"]["label"]}, "stance": position["stance"],
                      "source_expression_refs": [passage_ref(i) for i in cited],
                      "attribution_verbatim": position["attribution_verbatim"],
                      "provenance": provenance(position.get("created_by", "investigator"))}
            positions.append({"type": "source_position", **emit("source_position", f"{position['id']}:{claim['key']}", record)})
        if claim["attributed"] is True and not positions:
            raise BuildError(f"claim {claim['key']} is attributed but finding {finding_id} has no source position; "
                             "record who said it before ingesting")
        # The assessment comes from the fact-check row for this claim.
        candidates = [r for r in rows if r.get("finding_id") == finding_id]
        if "fact_check_id" in claim:
            candidates = [r for r in candidates if str(r.get("id")) == str(claim["fact_check_id"])]
        if len(candidates) != 1:
            raise BuildError(f"claim {claim['key']}: {len(candidates)} fact-check rows match finding {finding_id}; name one with fact_check_id")
        row = candidates[0]
        verdict, confidence = row.get("verdict"), row.get("confidence")
        if verdict not in VERDICTS_1_1 or confidence not in ("high", "medium", "low"):
            raise BuildError(f"claim {claim['key']}: the fact-check row needs a 1.1 verdict and a confidence")
        rationale = (row.get("grounding_assessment") or {}).get("assessment") or row.get("notes")
        if not _text(rationale):
            raise BuildError(f"claim {claim['key']}: the fact-check row has no rationale (grounding assessment or notes)")
        evidence = [e for e in (row.get("evidence_for") or []) + (row.get("evidence_against") or []) if isinstance(e, dict)]
        methods = []
        if any(e.get("access_method") in FETCHED for e in evidence):
            methods.append("re_fetched")
        if len({e.get("source") for e in evidence if _text(e.get("source"))}) >= 2:
            methods.append("second_source")
        assessment = {"claim_ref": ref, "verdict": verdict, "confidence": confidence, "rationale": rationale,
                      "provenance": provenance("fact-checker")}
        if methods:
            assessment["verification_methods"] = methods
        if groundings or positions:
            assessment["basis_refs"] = groundings + positions
        if verdict in ("verified", "disputed", "false") and not (methods and assessment.get("basis_refs")):
            raise BuildError(f"claim {claim['key']}: a {verdict} assessment needs a fetched source and linked passages")
        flags = []
        for index, flag in enumerate(row.get("flags") or []):
            if not isinstance(flag, dict) or flag.get("code") not in FLAG_CODES or not _text(flag.get("reason")):
                raise BuildError(f"claim {claim['key']}: fact-check flag {index} is malformed")
            key = flag_key(claim["key"], flag)
            decision = review.get(key)
            action = decision.get("final") if isinstance(decision, dict) else None
            note = decision.get("note") if isinstance(decision, dict) else None
            related = groundings or positions or [{"type": "claim", **ref}]
            if action not in ACTIONS or (action == "accept_with_reason" and not _text(note)):
                pending.append({"key": key, "claim_key": claim["key"], "code": flag["code"], "reason": flag["reason"]})
                continue
            disposition = {"action": action}
            if _text(note):
                disposition["reason"] = note
            flags.append({"code": flag["code"], "reason": flag["reason"], "related_refs": related, "disposition": disposition})
        if flags:
            assessment["flags"] = flags
        emit("assessment", claim["key"], assessment)

    event_refs: dict[str, dict[str, Any]] = {}
    for event in proposal.get("events", []):
        members = [k for k in event["claim_keys"] if k in claim_refs]
        if not selected(event["key"]) or not members:
            continue  # an event needs a claim asserting it (C7)
        record = {key: copy.deepcopy(event[key]) for key in ("language", "actors", "action", "object", "time", "place") if key in event}
        record["provenance"] = analyst
        event_refs[event["key"]] = emit("event", event["key"], record)
        for claim_key in members:
            emit("claim_event_membership", f"{claim_key}:{event['key']}",
                 {"claim_ref": claim_refs[claim_key], "event_ref": event_refs[event["key"]], "provenance": analyst})
    for arc in proposal.get("story_arcs", []):
        members = [k for k in arc["event_keys"] if k in event_refs]
        if not selected(arc["key"]) or not members:
            continue
        arc_ref = emit("story_arc", arc["key"], {"language": arc["language"], "title": arc["title"],
                                                 "description": arc["description"], "provenance": analyst})
        for event_key in members:
            emit("event_story_arc_membership", f"{event_key}:{arc['key']}",
                 {"event_ref": event_refs[event_key], "story_arc_ref": arc_ref, "provenance": analyst})

    ids.clear_unplaced()
    if pending and not draft:
        raise BuildError(f"{len(pending)} flag(s) await a disposition: " + ", ".join(p["key"] for p in pending[:8]))
    if not any(batch.get(array) for array in RULES.ARRAYS):
        if draft:
            return None, pending, ids.doc  # nothing new yet, but the review page still lists the pending flags
        raise BuildError("nothing new to submit: every selected record is already admitted unchanged")
    errors = schema_check.load(SCHEMAS / "knowledge-batch-1.1.schema.json").errors(batch)
    if errors:
        raise BuildError("the built batch does not match the Batch 1.1 core schema: " + "; ".join(errors[:8]))
    errors = RULES.validate(batch, [admitted_store(ids)])
    if errors:
        raise BuildError("the built batch breaks the cross-record rules: " + "; ".join(errors[:8]))
    return batch, pending, ids.doc


def admitted_store(ids: Ids) -> dict[str, Any]:
    """The admitted records, as kept in the id map at admission, so the rules
    check references, versions and lineage against them."""
    store: dict[str, Any] = {}
    for entry in ids.doc["records"].values():
        admitted = entry.get("admitted")
        if isinstance(admitted, dict) and isinstance(admitted.get("record"), dict):
            store.setdefault(ARRAY[entry["type"]], []).append(admitted["record"])
    return store


def write(case: Path, models: dict[str, str], draft: bool) -> dict[str, Any]:
    with transaction(case) as descriptor:
        try:
            batch, pending, ids = build(case, models, draft)
        except (KeyError, TypeError, AttributeError) as exc:
            raise BuildError(f"a case file is malformed ({type(exc).__name__}: {exc}); run validate-case.py") from exc
        dump = lambda doc: (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")  # noqa: E731
        out = {IDS: dump(ids)}
        if draft:
            out[DRAFT] = dump({"schema_version": "1.0", "batch": batch, "pending_flags": pending})
        else:
            out[BATCH] = dump(batch)
        atomic_write_files(case, out, data_descriptor=descriptor)
        if draft:
            # A draft supersedes any earlier final batch: until the review
            # finishes, there is no batch to ingest.
            (case / BATCH).unlink(missing_ok=True)
    records = sum(len(batch.get(a, [])) for a in RULES.ARRAYS) if batch else 0
    return {"ok": True, "draft": draft, "records": records, "pending_flags": len(pending)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("case_dir")
    parser.add_argument("--model", action="append", default=[], metavar="ACTOR=ID",
                        help="model identifier per producer (investigator, fact-checker)")
    parser.add_argument("--draft", action="store_true")
    args = parser.parse_args(argv)
    models = dict(item.split("=", 1) for item in args.model if "=" in item)
    try:
        result = write(Path(args.case_dir), models, args.draft)
    except (BuildError, OrchestrationError, OSError) as exc:
        print(f"build-knowledge-batch: refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
