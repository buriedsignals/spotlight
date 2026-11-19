"""Turn an approved Batch 1.1 into the newsroom service's ``start_case_review`` input.

The newsroom's Editorial MCP stores every record under a permanent UUID that
the harness asked its ``create_ids`` for before sending. A Batch 1.1 keeps
Spotlight's own ids, so this is the one place they are swapped: each record
carries its UUID as ``id``, and each reference names its target's type and
UUID.

``record_ids`` maps ``(record type, Batch 1.1 id)`` to that UUID. A record
keeps the same UUID in every version and in every batch of its case.

Two things in a Batch 1.1 have no place in the service's input and stay in
the case: a claim's ``origin`` beyond its ``finding_id``, and an assessment's
``flags`` (the service raises its own at review).
"""

from __future__ import annotations

from typing import Any

# Record list -> record type, in the service's submission order.
RECORD_LISTS = {
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
# Untyped Batch 1.1 references: the field names the target's type.
REF_FIELDS = {
    "claim_ref": "claim",
    "source_expression_ref": "source_expression",
    "origin_source_expression_ref": "source_expression",
    "event_ref": "event",
    "story_arc_ref": "story_arc",
}
RecordIds = dict[tuple[str, str], str]


class Refused(Exception):
    pass


def record_keys(batch: dict[str, Any]) -> list[tuple[tuple[str, str], int]]:
    """Every record of the batch as ``((record type, Batch 1.1 id), version)``."""
    return [((record_type, str(record.get("id"))), record.get("version"))
            for name, record_type in RECORD_LISTS.items()
            for record in batch.get(name) or []]


def start_case_review_input(batch: dict[str, Any], case_id: str, batch_id: str,
                            record_ids: RecordIds) -> dict[str, Any]:
    def uuid_of(record_type: str, record_id: object, where: str) -> str:
        key = (record_type, str(record_id))
        if key not in record_ids:
            raise Refused(f"{where}: no newsroom id is kept for {record_type} {key[1]}")
        return record_ids[key]

    def ref(record_type: str, value: dict[str, Any], where: str) -> dict[str, Any]:
        converted = {"id": {"type": record_type, "uuid": uuid_of(record_type, value.get("id"), where)},
                     "version": value.get("version")}
        if "derivative_id" in value:
            converted["derivative_id"] = value["derivative_id"]
        return converted

    request: dict[str, Any] = {"case_id": case_id, "batch_id": batch_id}
    for name, record_type in RECORD_LISTS.items():
        records = []
        for index, record in enumerate(batch.get(name) or []):
            where = f"{name}[{index}]"
            fields = {key: value for key, value in record.items() if key not in ("id", "origin", "flags")}
            for field, target in REF_FIELDS.items():
                if field in fields:
                    fields[field] = ref(target, fields[field], f"{where}.{field}")
            if "origin_ref" in fields:
                fields["origin_ref"] = ref("evidence", fields["origin_ref"], f"{where}.origin_ref")
            if "source_expression_refs" in fields:
                fields["source_expression_refs"] = [
                    ref("source_expression", value, f"{where}.source_expression_refs[{position}]")
                    for position, value in enumerate(fields["source_expression_refs"])]
            if "basis_refs" in fields:
                fields["basis_refs"] = [
                    ref(str(value.get("type")), value, f"{where}.basis_refs[{position}]")
                    for position, value in enumerate(fields["basis_refs"])]
            if "direct_quote" in fields:
                fields["is_direct_quote"] = fields.pop("direct_quote")
            finding_id = (record.get("origin") or {}).get("finding_id")
            if record_type == "claim" and finding_id:
                fields["finding_id"] = finding_id
            records.append({"id": uuid_of(record_type, record.get("id"), where), **fields})
        if records:
            request[name] = records
    return request
