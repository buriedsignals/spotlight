#!/usr/bin/env python3
"""Validate the vault claims layer: claim notes, claims registry, alias index,
merge proposals, and master stats.

Default mode: validate tests/fixtures/claims-vault (expected to pass), then run
negative self-tests (fixture mutations expected to fail).

Real-vault mode: vault-claims-check.py --vault /path/to/vault
A vault that predates the claims layer (no claims/_registry.json) passes with a
notice — the layer is additive, absence is not an error.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "claims-vault"

ALLOWED_VERDICTS = {"verified", "partially_verified"}
LAYER_FOR_VERDICT = {"verified": "durable", "partially_verified": "lead"}
NEEDS_VERIFICATION_FOR_LAYER = {"durable": False, "lead": True}
PROPOSAL_STATUSES = {"open", "accepted", "rejected"}
CLAIM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*-f\d+$")
REQUIRED_NOTE_FIELDS = {
    "id", "project", "finding_id", "entities", "verdict", "confidence",
    "confidence_cap", "layer", "recorded", "verified", "verified_by",
    "needs_verification",
}
REQUIRED_REGISTRY_FIELDS = {
    "id", "project", "entities", "verdict", "layer", "recorded", "verified",
    "needs_verification", "file",
}
SOURCE_BLOCK_START = "<!-- spotlight-source-expressions:v1\n"
SOURCE_BLOCK_END = "\n-->"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def normalize(name: str) -> str:
    return " ".join(name.lower().split())


def load_json(path: Path, errors: list[str]) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.name}: unreadable or invalid JSON ({exc})")
        return None


def parse_frontmatter(text: str, name: str, errors: list[str]) -> dict[str, object] | None:
    """Minimal frontmatter parser for the generated note format:
    scalar `key: value` and inline lists `key: [a, b]`."""
    if not text.startswith("---\n"):
        errors.append(f"{name}: missing frontmatter")
        return None
    end = text.find("\n---", 4)
    if end == -1:
        errors.append(f"{name}: unterminated frontmatter")
        return None
    fields: dict[str, object] = {}
    for line in text[4:end].splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            fields[key.strip()] = (
                [item.strip().strip('"').strip("'") for item in inner.split(",") if item.strip()]
                if inner else []
            )
        else:
            fields[key.strip()] = raw.strip('"').strip("'")
    return fields


def note_body_sections(text: str) -> dict[str, str]:
    end = text.find("\n---", 4)
    body = text[end + 4:] if end != -1 else text
    sections: dict[str, str] = {}
    current = None
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = ""
        elif current is not None:
            sections[current] += line + "\n"
    return sections


def source_expression_block(text: str, name: str, errors: list[str]) -> dict | None:
    start = text.find(SOURCE_BLOCK_START)
    if start == -1:
        return None
    payload_start = start + len(SOURCE_BLOCK_START)
    end = text.find(SOURCE_BLOCK_END, payload_start)
    if end == -1:
        errors.append(f"{name}: unterminated source-expression block")
        return None
    try:
        block = json.loads(text[payload_start:end])
    except json.JSONDecodeError as exc:
        errors.append(f"{name}: invalid source-expression block ({exc})")
        return None
    if not isinstance(block, dict) or block.get("schema_version") != "1.0":
        errors.append(f"{name}: source-expression block must use schema_version 1.0")
        return None
    return block


def validate_vault(vault: Path) -> tuple[list[str], list[str]]:
    """Returns (errors, notices)."""
    errors: list[str] = []
    notices: list[str] = []

    claims_registry_path = vault / "claims" / "_registry.json"
    if not claims_registry_path.exists():
        notices.append("no claims/_registry.json — vault predates the claims layer (ok, additive)")
        return errors, notices

    registry = load_json(claims_registry_path, errors)
    if registry is None:
        return errors, notices
    if registry.get("schema_version") != "1.0":
        errors.append("claims/_registry.json: schema_version must be 1.0")

    entries = {e.get("id"): e for e in registry.get("claims", [])}

    if (vault / "source-expressions").exists() or (vault / "source-expressions.json").exists():
        errors.append("source expressions must be embedded in eligible claims, not independently registered")

    # --- Registry entry shape and verdict/layer consistency ---
    for cid, entry in entries.items():
        missing = REQUIRED_REGISTRY_FIELDS - set(entry)
        if missing:
            errors.append(f"claims registry {cid}: missing fields {sorted(missing)}")
            continue
        if not CLAIM_ID_RE.match(str(cid)):
            errors.append(f"claims registry {cid}: id not in {{project-id}}-f{{n}} format")
        if not str(cid).startswith(str(entry["project"]) + "-"):
            errors.append(f"claims registry {cid}: id does not start with project '{entry['project']}'")
        verdict = entry["verdict"]
        if verdict not in ALLOWED_VERDICTS:
            errors.append(f"claims registry {cid}: verdict '{verdict}' not allowed in claims layer")
        elif entry["layer"] != LAYER_FOR_VERDICT[verdict]:
            errors.append(f"claims registry {cid}: layer '{entry['layer']}' inconsistent with verdict '{verdict}'")
        if entry["layer"] in NEEDS_VERIFICATION_FOR_LAYER and \
                entry["needs_verification"] != NEEDS_VERIFICATION_FOR_LAYER[entry["layer"]]:
            errors.append(f"claims registry {cid}: needs_verification inconsistent with layer '{entry['layer']}'")
        if not (vault / str(entry["file"])).exists():
            errors.append(f"claims registry {cid}: note file {entry['file']} does not exist")

    # --- Note <-> registry parity (both directions) ---
    note_paths = sorted((vault / "claims").glob("*.md"))
    note_ids = {p.stem for p in note_paths}
    for cid in note_ids - set(entries):
        errors.append(f"claims/{cid}.md has no registry entry")
    for cid in set(entries) - note_ids:
        errors.append(f"claims registry {cid}: no note file in claims/")

    # --- Note frontmatter, sources, history ---
    # Entities registry comes in two shapes: spec {entities: [...]} and the
    # legacy live-vault shape {section, last_updated, items}.
    entity_registry = load_json(vault / "entities" / "_registry.json", errors) or {"entities": []}
    known_entities = {e["id"] for e in entity_registry.get("entities", entity_registry.get("items", []))}
    for path in note_paths:
        note_text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(note_text, path.name, errors)
        if fm is None:
            continue
        missing = REQUIRED_NOTE_FIELDS - set(fm)
        if missing:
            errors.append(f"{path.name}: frontmatter missing {sorted(missing)}")
            continue
        entry = entries.get(path.stem)
        if entry:
            for field in ("project", "verdict", "layer"):
                if str(fm[field]) != str(entry[field]):
                    errors.append(f"{path.name}: frontmatter {field} '{fm[field]}' != registry '{entry[field]}'")
        if fm["verdict"] not in ALLOWED_VERDICTS:
            errors.append(f"{path.name}: verdict '{fm['verdict']}' not allowed in claims layer")
        if str(fm.get("confidence_cap")) == "low":
            errors.append(f"{path.name}: confidence_cap 'low' fails the eligibility gate")
        for eid in fm.get("entities", []):
            if eid not in known_entities:
                errors.append(f"{path.name}: entity '{eid}' not in entities registry")
        sections = note_body_sections(note_text)
        sources = sections.get("Sources", "")
        if not any(line.strip().lstrip("-").strip() for line in sources.splitlines()):
            errors.append(f"{path.name}: Sources section empty — claims require source refs")
        if "Supersession History" not in sections:
            errors.append(f"{path.name}: missing Supersession History section")

        block = source_expression_block(note_text, path.name, errors)
        if block is None:
            continue  # Legacy expression-less claims remain valid.
        snapshots = block.get("snapshots")
        events = block.get("ingest_events")
        if not isinstance(snapshots, list) or not isinstance(events, list):
            errors.append(f"{path.name}: source-expression snapshots/events must be arrays")
            continue
        snapshot_ids: set[str] = set()
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                errors.append(f"{path.name}: source-expression snapshot must be an object")
                continue
            required = {
                "snapshot_id", "project", "expression_id", "expression_fingerprint",
                "finding_id", "finding_fingerprint", "relation", "link_fingerprint",
                "text", "anchor_ref", "anchor_sha256", "original_evidence_bundle_id",
                "original_artifact_sha256", "lifecycle_state", "lifecycle_events",
            }
            missing_snapshot = required - set(snapshot)
            if missing_snapshot:
                errors.append(f"{path.name}: snapshot missing {sorted(missing_snapshot)}")
                continue
            expected_id = (
                f"{snapshot['project']}:{snapshot['expression_id']}:"
                f"{snapshot['expression_fingerprint']}"
            )
            if snapshot["snapshot_id"] != expected_id:
                errors.append(f"{path.name}: snapshot_id does not match project/expression/fingerprint")
            if snapshot["snapshot_id"] in snapshot_ids:
                errors.append(f"{path.name}: duplicate snapshot {snapshot['snapshot_id']}")
            snapshot_ids.add(snapshot["snapshot_id"])
            if snapshot["project"] != fm["project"] or snapshot["finding_id"] != fm["finding_id"]:
                errors.append(f"{path.name}: snapshot does not belong to this claim")
            for field in (
                "expression_fingerprint", "finding_fingerprint", "link_fingerprint",
                "anchor_sha256", "original_artifact_sha256",
            ):
                if not SHA256_RE.match(str(snapshot[field])):
                    errors.append(f"{path.name}: snapshot {field} is not lowercase SHA-256")
            lifecycle = snapshot["lifecycle_events"]
            if not isinstance(lifecycle, list) or not lifecycle:
                errors.append(f"{path.name}: snapshot has no lifecycle history")
            elif not isinstance(lifecycle[-1], dict) or lifecycle[-1].get("event") != snapshot["lifecycle_state"]:
                errors.append(f"{path.name}: lifecycle_state does not match final lifecycle event")
        event_ids: set[str] = set()
        for event in events:
            if not isinstance(event, dict):
                errors.append(f"{path.name}: ingest event must be an object")
                continue
            event_id = event.get("event_id")
            if not SHA256_RE.match(str(event_id)):
                errors.append(f"{path.name}: ingest event_id is not lowercase SHA-256")
            if event_id in event_ids:
                errors.append(f"{path.name}: duplicate ingest event {event_id}")
            event_ids.add(str(event_id))
            if event.get("claim_id") != path.stem or event.get("project") != fm["project"]:
                errors.append(f"{path.name}: ingest event targets another claim")
            if not SHA256_RE.match(str(event.get("source_expression_input_sha256"))):
                errors.append(f"{path.name}: ingest event source input hash invalid")
            if not set(event.get("snapshot_ids", [])).issubset(snapshot_ids):
                errors.append(f"{path.name}: ingest event references an unknown snapshot")

    # --- Merge proposals schema (loaded first: the alias check below excuses
    #     collisions that carry a recorded proposal) ---
    proposal_pairs: set[frozenset[str]] = set()
    proposals = load_json(vault / "entities" / "_merge-proposals.json", errors)
    if proposals is not None:
        for prop in proposals.get("proposals", []):
            if prop.get("status") not in PROPOSAL_STATUSES:
                errors.append(f"_merge-proposals.json {prop.get('id')}: invalid status '{prop.get('status')}'")
            for eid in prop.get("entities", []):
                if eid not in known_entities:
                    errors.append(f"_merge-proposals.json {prop.get('id')}: unknown entity '{eid}'")
            proposal_pairs.add(frozenset(prop.get("entities", [])))

    # --- Alias index derivable from entity frontmatter ---
    alias_index = load_json(vault / "entities" / "_aliases.json", errors)
    if alias_index is not None:
        alias_map = alias_index.get("aliases", {})
        for value in set(alias_map.values()):
            if value not in known_entities:
                errors.append(f"_aliases.json: '{value}' is not a known entity id")
        for entity_path in sorted((vault / "entities").glob("*.md")):
            fm = parse_frontmatter(entity_path.read_text(encoding="utf-8"), entity_path.name, errors)
            if fm is None:
                continue
            for alias in fm.get("aliases", []):
                key = normalize(str(alias))
                mapped = alias_map.get(key)
                if mapped == fm.get("id"):
                    continue
                # A collision with a recorded merge proposal is a flagged,
                # human-gated state — not index drift.
                if mapped and frozenset({mapped, str(fm.get("id"))}) in proposal_pairs:
                    continue
                errors.append(
                    f"_aliases.json: alias '{key}' of {fm.get('id')} missing or mapped elsewhere"
                )

    # --- Master stats ---
    master = load_json(vault / "_registry.json", errors)
    if master is not None:
        stat = master.get("stats", {}).get("claims")
        if stat != len(entries) or stat != len(note_ids):
            errors.append(
                f"_registry.json: stats.claims={stat} but registry has {len(entries)} entries "
                f"and claims/ has {len(note_ids)} notes"
            )

    return errors, notices


# --- Negative self-tests: each mutation must produce at least one error ---

def _mutate_json(path: Path, fn) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def negative_cases() -> list[tuple[str, callable]]:
    def drop_registry_entry(v: Path):
        _mutate_json(v / "claims/_registry.json", lambda d: d["claims"].pop(0))
        _mutate_json(v / "_registry.json", lambda d: d["stats"].__setitem__("claims", 1))

    def orphan_registry_entry(v: Path):
        def add(d):
            ghost = dict(d["claims"][0], id="test-case-f9", file="claims/test-case-f9.md")
            d["claims"].append(ghost)
        _mutate_json(v / "claims/_registry.json", add)

    def empty_sources(v: Path):
        p = v / "claims/test-case-f1.md"
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("- https://example.org/registry/filing-123 (accessed 2026-06-01)\n", ""), encoding="utf-8")

    def missing_alias(v: Path):
        _mutate_json(v / "entities/_aliases.json", lambda d: d["aliases"].pop("acme ltd"))

    def bad_verdict(v: Path):
        _mutate_json(v / "claims/_registry.json", lambda d: d["claims"][0].__setitem__("verdict", "disputed"))

    def stats_mismatch(v: Path):
        _mutate_json(v / "_registry.json", lambda d: d["stats"].__setitem__("claims", 5))

    def layer_inconsistent(v: Path):
        _mutate_json(v / "claims/_registry.json", lambda d: d["claims"][0].__setitem__("layer", "lead"))

    def low_confidence_cap(v: Path):
        p = v / "claims/test-case-f1.md"
        p.write_text(p.read_text(encoding="utf-8").replace("confidence_cap: high", "confidence_cap: low"), encoding="utf-8")

    def missing_supersession(v: Path):
        p = v / "claims/test-case-f1.md"
        p.write_text(p.read_text(encoding="utf-8").replace("## Supersession History", "## History"), encoding="utf-8")

    def bad_proposal_status(v: Path):
        def add(d):
            d["proposals"].append({"id": "merge-0001", "entities": ["acme-corp", "john-doe"],
                                   "colliding_alias": "x", "source_project": "test-case",
                                   "proposed": "2026-06-01", "status": "maybe"})
        _mutate_json(v / "entities/_merge-proposals.json", add)

    def standalone_expression_registry(v: Path):
        (v / "source-expressions.json").write_text("{}\n", encoding="utf-8")

    return [
        ("note without registry entry", drop_registry_entry),
        ("registry entry without note", orphan_registry_entry),
        ("claim without sources", empty_sources),
        ("alias missing from index", missing_alias),
        ("verdict outside claims-layer enum", bad_verdict),
        ("master stats mismatch", stats_mismatch),
        ("layer inconsistent with verdict", layer_inconsistent),
        ("confidence_cap low fails eligibility", low_confidence_cap),
        ("missing Supersession History section", missing_supersession),
        ("invalid merge-proposal status", bad_proposal_status),
        ("standalone expression registry", standalone_expression_registry),
    ]


def strip_source_block(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    heading = "\n\n## Source Expressions\n\n"
    start = text.find(heading)
    if start != -1:
        path.write_text(text[:start].rstrip() + "\n", encoding="utf-8")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def build_case(case: Path) -> tuple[str, str, str]:
    data = case / "data"
    data.mkdir(parents=True)
    finding_fp = "1" * 64
    expression_fps = ("2" * 64, "3" * 64, "4" * 64)
    link_fps = ("5" * 64, "6" * 64, "7" * 64)
    findings = {
        "schema_version": "1.1",
        "project": "test-case",
        "findings": [
            {"id": "F1", "claim": "one", "finding_fingerprint": finding_fp,
             "evidence": "one", "sources": [{"url": "https://one.example"}],
             "confidence": "high", "grounding": {"confidence_cap": "high"}},
            {"id": "F2", "claim": "two", "finding_fingerprint": "8" * 64,
             "evidence": "two", "sources": [{"url": "https://two.example"}],
             "confidence": "medium", "grounding": {"confidence_cap": "medium"}},
            {"id": "F3", "claim": "three", "finding_fingerprint": "9" * 64,
             "evidence": "three", "sources": [{"url": "https://three.example"}],
             "confidence": "low", "grounding": {"confidence_cap": "low"}},
        ],
    }
    checked = {
        "schema_version": "1.0", "project": "test-case", "claims": [
            {"finding_id": "F1", "claim_text": "one", "verdict": "verified",
             "grounding_assessment": {"confidence_cap": "high"}},
            {"finding_id": "F2", "claim_text": "two", "verdict": "partially_verified",
             "grounding_assessment": {"confidence_cap": "medium"}},
            {"finding_id": "F3", "claim_text": "three", "verdict": "disputed",
             "grounding_assessment": {"confidence_cap": "low"}},
        ],
    }
    expressions = []
    for number, (fid, expression_fp, link_fp, finding_hash) in enumerate(zip(
        ("F1", "F2", "F3"), expression_fps, link_fps,
        (finding_fp, "8" * 64, "9" * 64)
    ), start=1):
        expressions.append({
            "id": f"SX-{fid}-01", "text": f"exact passage {number}",
            "anchor_ref": {"path": f"research/source-{number}.md", "line_start": 1, "line_end": 1},
            "anchor_sha256": chr(96 + number) * 64,
            "original_evidence_bundle_id": f"E{number}",
            "original_artifact_sha256": chr(99 + number) * 64,
            "expression_fingerprint": expression_fp,
            "finding_links": [{"finding_id": fid, "finding_fingerprint": finding_hash,
                               "relation": "supports", "link_fingerprint": link_fp}],
            "lifecycle_events": [{"event": "activated", "timestamp": "2026-07-16T00:00:00Z",
                                  "actor": "fact-checker", "reason": "verified exact anchor"}],
            "created_by": "fact-checker", "cycle": 1,
        })
    source_doc = {"schema_version": "1.0", "project": "test-case",
                  "created_at": "2026-07-16T00:00:00Z", "expressions": expressions}
    write_json(data / "findings.json", findings)
    write_json(data / "fact-check.json", checked)
    write_json(data / "source-expressions.json", source_doc)
    write_json(data / "evidence-bundle.json", {})
    hashes = {
        "findings_sha256": sha((data / "findings.json").read_bytes()),
        "fact_check_sha256": sha((data / "fact-check.json").read_bytes()),
        "evidence_bundle_sha256": sha((data / "evidence-bundle.json").read_bytes()),
        "source_expressions_sha256": sha((data / "source-expressions.json").read_bytes()),
    }
    write_json(data / "case-contract.json", {
        "schema_version": "1.0", "project": "test-case", "current_contract_version": "1.1",
        "activation_events": [{
            "event_id": "activate-vault-fixture",
            "previous_contract_version": "1.0",
            "activated_contract_version": "1.1",
            "activated_at": "2026-07-16T00:00:00Z",
            "tool_version": "test/1",
            "prior_input_hashes": {key: value for key, value in hashes.items() if key != "source_expressions_sha256"},
            "activated_artifact_hashes": hashes,
        }],
    })
    write_json(data / "ingestion.json", {"schema_version": "1.0", "status": "completed"})
    return expression_fps


def run_writer_tests() -> int:
    spec = importlib.util.spec_from_file_location(
        "ingest_source_expressions", ROOT / "scripts" / "ingest-source-expressions.py"
    )
    assert spec and spec.loader
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        vault = base / "vault"
        shutil.copytree(FIXTURE, vault)
        for note in (vault / "claims").glob("*.md"):
            strip_source_block(note)
        case = base / "case"
        expression_fps = build_case(case)
        legacy_note = vault / "claims" / "legacy-case-f1.md"
        legacy_note.write_text((vault / "claims" / "test-case-f1.md").read_text().replace(
            "id: test-case-f1", "id: legacy-case-f1"
        ).replace("project: test-case", "project: legacy-case").replace("finding_id: F1", "finding_id: F1"), encoding="utf-8")
        before_legacy = legacy_note.read_bytes()
        registry = json.loads((vault / "claims/_registry.json").read_text())
        legacy_entry = dict(registry["claims"][0], id="legacy-case-f1", project="legacy-case",
                            file="claims/legacy-case-f1.md")
        registry["claims"].append(legacy_entry)
        write_json(vault / "claims/_registry.json", registry)
        master = json.loads((vault / "_registry.json").read_text())
        master["stats"]["claims"] = 3
        write_json(vault / "_registry.json", master)

        receipt = writer.ingest(case, vault)
        first_bytes = {path: path.read_bytes() for path in [
            vault / "claims/test-case-f1.md", vault / "claims/test-case-f2.md",
            legacy_note, case / "data/ingestion.json",
        ]}
        writer.ingest(case, vault)
        second_bytes = {path: path.read_bytes() for path in first_bytes}
        if first_bytes != second_bytes:
            print("FAIL source-expression re-ingest was not byte-identical")
            failures += 1
        elif before_legacy != legacy_note.read_bytes():
            print("FAIL expression-less legacy claim changed")
            failures += 1
        elif len(receipt["written_snapshot_ids"]) != 2 or len(receipt["skipped"]) != 1:
            print("FAIL eligibility receipt did not record 2 written / 1 disputed skipped")
            failures += 1
        elif not all(expression_fps[i] in first_bytes[path].decode() for i, path in enumerate([
            vault / "claims/test-case-f1.md", vault / "claims/test-case-f2.md"
        ])):
            print("FAIL eligible expression snapshot missing from claim")
            failures += 1
        else:
            print("ok   deterministic writer admits verified/partial, skips disputed, preserves legacy")

        source_path = case / "data/source-expressions.json"
        source_doc = json.loads(source_path.read_text())
        source_doc["expressions"][0]["lifecycle_events"].append({
            "event": "withdrawn", "timestamp": "2026-07-17T00:00:00Z",
            "actor": "human", "reason": "source was retracted",
        })
        write_json(source_path, source_doc)
        contract_path = case / "data/case-contract.json"
        contract = json.loads(contract_path.read_text())
        contract["activation_events"][-1]["activated_artifact_hashes"][
            "source_expressions_sha256"
        ] = sha(source_path.read_bytes())
        write_json(contract_path, contract)
        writer.ingest(case, vault)
        withdrawn_bytes = (vault / "claims/test-case-f1.md").read_bytes()
        writer.ingest(case, vault)
        if b'"lifecycle_state": "withdrawn"' not in withdrawn_bytes:
            print("FAIL inactive source-expression history was not preserved")
            failures += 1
        elif withdrawn_bytes != (vault / "claims/test-case-f1.md").read_bytes():
            print("FAIL inactive source-expression re-ingest was not byte-identical")
            failures += 1
        else:
            print("ok   lifecycle withdrawal is append-only and idempotent")

        lock = vault / ".ingest-lock"
        lock.write_text("test-case 2026-07-17T00:00:00Z\n", encoding="utf-8")
        writer.ingest(case, vault, lock_held=True)
        if not lock.exists():
            print("FAIL writer removed caller-owned ingest lock")
            failures += 1
        else:
            print("ok   writer reuses and preserves the project-owned ingest lock")
        lock.unlink(missing_ok=True)

        errors, _ = validate_vault(vault)
        if errors:
            print("FAIL writer output does not validate:")
            for error in errors:
                print(f"  - {error}")
            failures += 1

        rollback_vault = base / "rollback-vault"
        shutil.copytree(FIXTURE, rollback_vault)
        for note in (rollback_vault / "claims").glob("*.md"):
            strip_source_block(note)
        originals = {path: path.read_bytes() for path in (rollback_vault / "claims").glob("*.md")}
        calls = 0

        def fail_second(path: Path, content: bytes) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected partial publication failure")
            writer.atomic_replace(path, content)

        try:
            writer.ingest(case, rollback_vault, writer=fail_second)
            print("FAIL injected partial publication did not fail")
            failures += 1
        except writer.IngestError:
            if (rollback_vault / ".ingest-lock").exists():
                print("FAIL ingest lock survived publication failure")
                failures += 1
            elif any(path.read_bytes() != content for path, content in originals.items()):
                print("FAIL partial publication was not rolled back")
                failures += 1
            else:
                print("ok   partial publication rolls back and cleans lock")
    return failures


def run_self_tests() -> int:
    failures = 0
    errors, _ = validate_vault(FIXTURE)
    if errors:
        print("FAIL fixture vault should validate cleanly:")
        for err in errors:
            print(f"  - {err}")
        failures += 1
    else:
        print("ok   fixture vault validates")

    for name, mutate in negative_cases():
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "vault"
            shutil.copytree(FIXTURE, copy)
            mutate(copy)
            errors, _ = validate_vault(copy)
            if errors:
                print(f"ok   rejects: {name}")
            else:
                print(f"FAIL not rejected: {name}")
                failures += 1
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, help="validate a real vault instead of running self-tests")
    args = parser.parse_args()

    if args.vault:
        if not args.vault.is_dir():
            print(f"FAIL not a directory: {args.vault}")
            return 1
        errors, notices = validate_vault(args.vault)
        for note in notices:
            print(f"note {note}")
        if errors:
            print(f"FAIL {args.vault}: {len(errors)} error(s)")
            for err in errors:
                print(f"  - {err}")
            return 1
        print(f"ok   {args.vault}: claims layer valid")
        return 0

    return 1 if run_self_tests() + run_writer_tests() else 0


if __name__ == "__main__":
    sys.exit(main())
