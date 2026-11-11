"""Turn an approved Batch 1.1 into the newsroom service's ``start_case_review`` input.

The newsroom's Editorial MCP stores a batch under server-issued ids: the case
id and the batch id come from its ``create_id``, a new record carries a
``client_id`` (its Batch 1.1 id) at version 1, and a new version of a record
the service already holds carries that record's ``existing_server_id``. Every
reference names its target's type and one of the same two ids.

``server_ids`` maps ``(record type, Batch 1.1 id)`` to the uuid the service
assigned in an earlier submission of this case (its ``assigned_ids``).

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
ServerIds = dict[tuple[str, str], str]


class Refused(Exception):
    pass


def start_case_review_input(batch: dict[str, Any], case_id: str, batch_id: str,
                            server_ids: ServerIds | None = None) -> dict[str, Any]:
    known = server_ids or {}
    new = {(record_type, str(record.get("id")))
           for name, record_type in RECORD_LISTS.items()
           for record in batch.get(name) or [] if record.get("version") == 1}

    def identity(record_type: str, record_id: object, where: str) -> dict[str, str]:
        key = (record_type, str(record_id))
        if key in new:
            return {"client_id": key[1]}
        if key in known:
            return {"existing_server_id": known[key]}
        raise Refused(f"{where}: the newsroom has not assigned an id to {record_type} {key[1]}")

    def ref(record_type: str, value: dict[str, Any], where: str) -> dict[str, Any]:
        converted = {"id": {"type": record_type, **identity(record_type, value.get("id"), where)},
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
            records.append({**identity(record_type, record.get("id"), where), **fields})
        if records:
            request[name] = records
    return request
