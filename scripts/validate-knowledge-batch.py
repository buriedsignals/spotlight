#!/usr/bin/env python3
"""Check a Knowledge Batch 1.1 against the cross-record rules (stdlib only).

  python3 scripts/validate-knowledge-batch.py BATCH [--admitted FILE ...] [--json]

JSON Schema cannot check these rules alone; they need the submitted batch and
the admitted store together. ``--admitted`` names batches already admitted
(in any order). The batch must already validate against
schemas/knowledge-batch-1.1.schema.json; this script does not repeat that.

Rules (Batch 1.1 semantic rules, section 17):

  C1   every submitted (type, id, version) is new
  C2   version n > 1 has version n - 1 in the batch or the store
  C3   every exact reference resolves to the expected record type and version
  C4   a passage's derivative_id exists in its exact origin version
  C5   claim origins, grounding records and positions use original passages
  C6   later evidence versions keep the original hash
  C7   every event version has at least one claim membership
  C8   every assessment flag code is in the registry
  C9   grounding records and positions in an assessment basis concern the
       assessed claim version
  C10  a later relationship version keeps its lineage pair, and no second id
       links the same pair
  C13  dates and date-times are real calendar values, and an event interval
       does not end before it starts

C11 does not exist and C12 (retractions) belongs to the newsroom edition.
Passing proves structure and references, not truth: people still review.

Exit codes: 0 = pass; 3 = at least one rule fails; 2 = unreadable input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from verdicts import FLAG_CODES  # noqa: E402

# Batch array -> record type.
ARRAYS = {
    "evidence": "evidence",
    "source_expressions": "source_expression",
    "claims": "claim",
    "claim_source_expressions": "claim_source_expression",
    "source_positions": "source_position",
    "assessments": "assessment",
    "events": "event",
    "claim_event_memberships": "claim_event_membership",
    "story_arcs": "story_arc",
    "event_story_arc_memberships": "event_story_arc_membership",
}

# Exact references whose field fixes the target type: (record type, field) ->
# target type. A list-valued field holds several references.
EXACT_REFS = {
    ("claim", "origin_source_expression_ref"): "source_expression",
    ("claim_source_expression", "claim_ref"): "claim",
    ("claim_source_expression", "source_expression_ref"): "source_expression",
    ("source_position", "claim_ref"): "claim",
    ("source_position", "source_expression_refs"): "source_expression",
    ("assessment", "claim_ref"): "claim",
    ("claim_event_membership", "claim_ref"): "claim",
    ("claim_event_membership", "event_ref"): "event",
    ("event_story_arc_membership", "event_ref"): "event",
    ("event_story_arc_membership", "story_arc_ref"): "story_arc",
}

# Lineage each record id stands for (semantic rules, "Relationship identity"):
# a relationship id is one pair of endpoint lineages; a position or an
# assessment id is one claim lineage. Positions also carry an actor, but two
# positions of one actor on one claim are not forbidden by the rules.
LINEAGE = {
    "claim_source_expression": ("claim_ref", "source_expression_ref"),
    "claim_event_membership": ("claim_ref", "event_ref"),
    "event_story_arc_membership": ("event_ref", "story_arc_ref"),
    "source_position": ("claim_ref",),
    "assessment": ("claim_ref",),
}
# Only true relationships forbid a second id on the same pair.
UNIQUE_PAIR = ("claim_source_expression", "claim_event_membership", "event_story_arc_membership")

_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
_WEEK = re.compile(r"^(\d{4})-W(\d{2})$")
Key = tuple[str, str, int]


def records(batch: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for array, record_type in ARRAYS.items():
        for record in batch.get(array) or []:
            if isinstance(record, dict):
                yield record_type, record


def key(record_type: str, record: dict[str, Any]) -> Key:
    return (record_type, str(record.get("id")), record.get("version"))


def ref_key(record_type: str, ref: Any) -> Key | None:
    if not isinstance(ref, dict):
        return None
    return (record_type, str(ref.get("id")), ref.get("version"))


def label(k: Key) -> str:
    return f"{k[0]} {k[1]!r} v{k[2]}"


def outgoing_refs(record_type: str, record: dict[str, Any]) -> Iterator[tuple[str, Key]]:
    """Every (field, target key) a record points at."""
    for (owner, field), target in EXACT_REFS.items():
        if owner != record_type or field not in record:
            continue
        values = record[field] if isinstance(record[field], list) else [record[field]]
        for value in values:
            k = ref_key(target, value)
            if k:
                yield field, k
    if record_type == "source_expression" and isinstance(record.get("origin_ref"), dict):
        origin = record["origin_ref"]
        yield "origin_ref", (str(origin.get("type")), str(origin.get("id")), origin.get("version"))
    if record_type == "assessment":
        for ref in record.get("basis_refs") or []:
            if isinstance(ref, dict):
                yield "basis_refs", (str(ref.get("type")), str(ref.get("id")), ref.get("version"))
        for flag in record.get("flags") or []:
            for ref in (flag.get("related_refs") or []) if isinstance(flag, dict) else []:
                if isinstance(ref, dict):
                    yield "flags.related_refs", (str(ref.get("type")), str(ref.get("id")), ref.get("version"))


def real_time_value(value: str) -> tuple[datetime, datetime] | None:
    """The earliest and latest instant a time value can mean, or None if impossible.

    Coarser values have no offset; a timestamp is compared in UTC. The schema pattern has already fixed the
    shape, so this only rejects calendar values that cannot exist.
    """
    try:
        if len(value) == 4:
            year = int(value)
            return datetime(year, 1, 1), datetime.combine(date(year, 12, 31), datetime.max.time())
        week = _WEEK.match(value)
        if week:
            start = date.fromisocalendar(int(week[1]), int(week[2]), 1)
            end = date.fromisocalendar(int(week[1]), int(week[2]), 7)
            return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.max.time())
        if len(value) == 7:
            year, month = int(value[:4]), int(value[5:7])
            start = date(year, month, 1)
            last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
            return datetime.combine(start, datetime.min.time()), datetime.combine(last, datetime.max.time())
        if len(value) == 10:
            day = date.fromisoformat(value)
            return datetime.combine(day, datetime.min.time()), datetime.combine(day, datetime.max.time())
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment
        return moment, moment
    except ValueError:
        return None


def validate(batch: dict[str, Any], admitted: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    store: dict[Key, dict[str, Any]] = {}
    for doc in admitted:
        for record_type, record in records(doc):
            store[key(record_type, record)] = record

    submitted: dict[Key, dict[str, Any]] = {}
    for record_type, record in records(batch):
        k = key(record_type, record)
        # C1
        if k in submitted:
            errors.append(f"C1: {label(k)} appears twice in the batch")
        elif k in store:
            errors.append(f"C1: {label(k)} is already admitted")
        submitted[k] = record
    known = {**store, **submitted}

    for k, record in submitted.items():
        record_type, record_id, version = k
        # C2
        if isinstance(version, int) and version > 1 and (record_type, record_id, version - 1) not in known:
            errors.append(f"C2: {label(k)} has no version {version - 1} in the batch or the store")
        # C3
        for field, target in outgoing_refs(record_type, record):
            if target not in known:
                errors.append(f"C3: {label(k)} {field} points at {label(target)}, which is not in the batch or the store")
        # C4
        origin = record.get("origin_ref") if record_type == "source_expression" else None
        if isinstance(origin, dict) and "derivative_id" in origin:
            evidence = known.get((str(origin.get("type")), str(origin.get("id")), origin.get("version")))
            derivative_ids = {d.get("id") for d in (evidence or {}).get("derivatives") or [] if isinstance(d, dict)}
            if evidence is not None and origin["derivative_id"] not in derivative_ids:
                errors.append(f"C4: {label(k)} derivative_id {origin['derivative_id']!r} is not a derivative of its origin version")
        # C5: a passage that is a translation (full-schema translation_of_ref)
        # may never ground a claim, a grounding record or a position.
        if record_type in ("claim", "claim_source_expression", "source_position"):
            for field, target in outgoing_refs(record_type, record):
                passage = known.get(target) if target[0] == "source_expression" else None
                if passage is not None and "translation_of_ref" in passage:
                    errors.append(f"C5: {label(k)} {field} uses translated passage {label(target)}; point at the original")
        # C6
        if record_type == "evidence" and isinstance(version, int) and version > 1:
            first = next((known[(record_type, record_id, v)] for v in range(1, version) if (record_type, record_id, v) in known), None)
            if first is not None and first.get("content_sha256") != record.get("content_sha256"):
                errors.append(f"C6: {label(k)} changes the original content_sha256")
        # C8, C9
        if record_type == "assessment":
            for flag in record.get("flags") or []:
                code = flag.get("code") if isinstance(flag, dict) else None
                if code not in FLAG_CODES:
                    errors.append(f"C8: {label(k)} flag code {code!r} is not in the registry")
            assessed = ref_key("claim", record.get("claim_ref"))
            for _, target in outgoing_refs(record_type, record):
                if target[0] not in ("claim_source_expression", "source_position"):
                    continue
                basis = known.get(target)
                if basis is not None and ref_key("claim", basis.get("claim_ref")) != assessed:
                    errors.append(f"C9: {label(k)} basis {label(target)} concerns a different claim version")
        # C13
        if record_type == "evidence":
            captured = ((record.get("capture") or {}).get("captured_at")) or ""
            if not _DATE_TIME.match(captured) or real_time_value(captured) is None:
                errors.append(f"C13: {label(k)} captured_at {captured!r} is not a real date-time")
        if record_type == "event" and isinstance(record.get("time"), dict):
            span = {f: real_time_value(record["time"][f]) for f in ("value", "start", "end") if isinstance(record["time"].get(f), str)}
            for field, bounds in span.items():
                if bounds is None:
                    errors.append(f"C13: {label(k)} time.{field} {record['time'][field]!r} is not a real calendar value")
            if span.get("start") and span.get("end") and span["end"][1] < span["start"][0]:
                errors.append(f"C13: {label(k)} interval ends before it starts")

    # C7
    members = {ref_key("event", r.get("event_ref")) for k, r in known.items() if k[0] == "claim_event_membership"}
    for k in submitted:
        if k[0] == "event" and k not in members:
            errors.append(f"C7: {label(k)} has no claim membership")

    # C10
    pairs: dict[tuple[str, tuple[str, ...]], str] = {}
    lineage_of: dict[tuple[str, str], tuple[str, ...]] = {}
    for k in sorted(known, key=lambda k: (k[0], k[1], k[2] if isinstance(k[2], int) else 0)):
        if k[0] not in LINEAGE:
            continue
        record = known[k]
        lineage = tuple(str((record.get(f) or {}).get("id")) for f in LINEAGE[k[0]])
        previous = lineage_of.setdefault((k[0], k[1]), lineage)
        if previous != lineage and k in submitted:
            errors.append(f"C10: {label(k)} changes its lineage from {previous} to {lineage}")
        if k[0] in UNIQUE_PAIR:
            owner = pairs.setdefault((k[0], lineage), k[1])
            if owner != k[1] and k in submitted:
                errors.append(f"C10: {label(k)} links the same pair {lineage} as {owner!r}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("batch")
    parser.add_argument("--admitted", nargs="*", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        batch = json.loads(Path(args.batch).read_text(encoding="utf-8"))
        admitted = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.admitted]
    except (OSError, ValueError) as exc:
        print(f"validate-knowledge-batch: cannot read input: {exc}", file=sys.stderr)
        return 2
    if not isinstance(batch, dict) or batch.get("schema_version") != "1.1":
        print("validate-knowledge-batch: not a Batch 1.1 document", file=sys.stderr)
        return 2
    errors = validate(batch, [d for d in admitted if isinstance(d, dict)])
    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors}, indent=2))
    else:
        for error in errors:
            print(error)
        print("ok" if not errors else f"{len(errors)} cross-record failure(s)")
    return 3 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
