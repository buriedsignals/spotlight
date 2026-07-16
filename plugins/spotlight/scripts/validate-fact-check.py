#!/usr/bin/env python3
"""Validate fact-check evidence through language-neutral, case-local anchors.

The validator deliberately does not tokenize, translate, or semantically grade prose.
For every ``verified`` or ``partially_verified`` verdict it proves that:

* the fact-check claim exactly matches its linked finding after Unicode/whitespace
  normalization;
* at least one fact-check evidence item resolves to a file inside the case;
* declared line ranges, JSON Pointers, exact quotes, and SHA-256 values are valid;
* every evidence-bundle reference resolves to an intact case-local artifact; and
* no referenced bundle item is flagged for fallback or human verification.

RLM/E4B extraction may supply ``source_ref`` locations, but the underlying stored
scrape or JSON value is the anchor. RLM prose is never treated as verified evidence.
These checks prove provenance and integrity, not semantic entailment; the independent
fact-checker and human editorial gate remain responsible for interpreting the source.

Exit codes: 0 = evidence anchors pass; 3 = at least one failure.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence_anchors import (
    case_evidence_path,
    normalized,
    rlm_lead_failure,
    sha256_file,
    validate_source_ref,
)


VERDICTS = {
    "verified",
    "partially_verified",
    "unverified",
    "disputed",
    "false",
    "mischaracterized",
}
ANCHORED_VERDICTS = {"verified", "partially_verified"}
ARTIFACT_PATH_KEYS = ("raw_path", "downloaded_document_path", "screenshot_path", "path")
def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        raise ValueError(f"missing {path}") from None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise ValueError(f"invalid {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def canonical_fact_checks(document: dict[str, Any]) -> list[dict[str, Any]]:
    container = next(
        (key for key in ("claims", "fact_checks", "verdicts") if isinstance(document.get(key), list)),
        "",
    )
    raw = document.get(container)
    if not isinstance(raw, list):
        return []

    checks: list[dict[str, Any]] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            continue
        check = dict(item)
        check["id"] = item.get("id", f"FC{index}")
        if container == "claims":
            check["status"] = normalized(item.get("verdict")).lower()
            check["claim"] = item.get("claim_text") or ""
        elif container == "fact_checks":
            check["status"] = normalized(item.get("status")).lower()
            check["claim"] = item.get("claim") or ""
        else:
            current = "claim_text" in item or "verdict" in item
            check["status"] = normalized(item.get("verdict") if current else item.get("status")).lower()
            check["claim"] = (item.get("claim_text") if current else item.get("claim")) or ""
        checks.append(check)
    return checks


def bundle_items(
    document: dict[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if document is None:
        return {}, []
    raw = document.get("items")
    if not isinstance(raw, list):
        raw = document.get("evidence")
    if not isinstance(raw, list):
        return {}, []
    indexed: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for item in raw:
        if not isinstance(item, dict) or not normalized(item.get("id")):
            continue
        evidence_id = normalized(item.get("id"))
        if evidence_id in indexed:
            failures.append(f"evidence bundle contains duplicate id {evidence_id!r}")
            continue
        indexed[evidence_id] = item
    return indexed, failures


def bundle_finding_ids(item: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    if normalized(item.get("finding_id")):
        ids.add(normalized(item.get("finding_id")))
    links = item.get("claim_links")
    if isinstance(links, list):
        for link in links:
            if isinstance(link, dict) and normalized(link.get("finding_id")):
                ids.add(normalized(link.get("finding_id")))
            elif isinstance(link, str) and normalized(link):
                ids.add(normalized(link))
    return ids


def validate_bundle_item(
    case_dir: Path,
    item: dict[str, Any],
    finding_id: str,
    finding_claim: str,
    *,
    canonical: bool,
) -> tuple[set[Path], list[str]]:
    evidence_id = normalized(item.get("id")) or "?"
    failures: list[str] = []
    claim_links = item.get("claim_links")
    linked = bundle_finding_ids(item)
    if canonical:
        linked = {
            normalized(link.get("finding_id"))
            for link in claim_links or []
            if isinstance(link, dict) and normalized(link.get("finding_id"))
        } if isinstance(claim_links, list) else set()
    if not linked:
        failures.append(f"evidence bundle {evidence_id} has no explicit finding link")
    elif finding_id not in linked:
        failures.append(f"evidence bundle {evidence_id} is not linked to finding {finding_id}")
    if canonical and not isinstance(claim_links, list):
        failures.append(f"evidence bundle {evidence_id} needs structured claim_links")
    if isinstance(claim_links, list):
        matching_links = [
            link for link in claim_links
            if isinstance(link, dict) and normalized(link.get("finding_id")) == finding_id
        ]
        if canonical and not matching_links:
            failures.append(
                f"evidence bundle {evidence_id} has no structured link to finding {finding_id}"
            )
        for link in matching_links:
            if normalized(link.get("claim_text")) != normalized(finding_claim):
                failures.append(
                    f"evidence bundle {evidence_id} claim link does not exactly match finding {finding_id}"
                )
            if link.get("support_type") not in {"direct", "indirect"}:
                failures.append(
                    f"evidence bundle {evidence_id} links finding {finding_id} as "
                    f"{link.get('support_type')!r}, not positive evidence"
                )
    if canonical and item.get("human_verification_required") is not False:
        failures.append(
            f"evidence bundle {evidence_id} must set human_verification_required to false"
        )
    elif item.get("human_verification_required") is True:
        failures.append(f"evidence bundle {evidence_id} requires human verification")
    gate = item.get("missing_source_gate")
    if canonical and isinstance(gate, bool):
        failures.append(f"evidence bundle {evidence_id} has malformed missing_source_gate")
    if gate is not None and not isinstance(gate, (dict, bool)):
        failures.append(f"evidence bundle {evidence_id} has malformed missing_source_gate")
    if isinstance(gate, dict):
        if not isinstance(gate.get("fallback_required"), bool):
            failures.append(
                f"evidence bundle {evidence_id} missing_source_gate needs boolean fallback_required"
            )
        elif gate["fallback_required"]:
            failures.append(f"evidence bundle {evidence_id} still requires a source fallback")

    declared = [item.get(key) for key in ARTIFACT_PATH_KEYS if normalized(item.get(key))]
    if not declared:
        failures.append(f"evidence bundle {evidence_id} has no stored artifact path")
        return set(), failures

    paths: set[Path] = set()
    for raw_path in declared:
        path = case_evidence_path(case_dir, raw_path)
        if path is None:
            failures.append(
                f"evidence bundle {evidence_id} path {raw_path!r} does not resolve to a case-local file"
            )
        else:
            paths.add(path)
            lead_failure = rlm_lead_failure(path)
            if lead_failure:
                failures.append(f"evidence bundle {evidence_id}: {lead_failure}")

    expected_hash = normalized(item.get("sha256")).lower()
    if expected_hash:
        if not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
            failures.append(f"evidence bundle {evidence_id} sha256 is not 64 hexadecimal characters")
        elif paths and not any(sha256_file(path) == expected_hash for path in paths):
            failures.append(f"evidence bundle {evidence_id} sha256 does not match its artifact")
    return paths, failures


def validate_evidence_entry(
    case_dir: Path,
    entry: dict[str, Any],
    finding_id: str,
    finding_claim: str,
    bundles: dict[str, dict[str, Any]],
    *,
    canonical_bundle: bool,
    verdict: str,
) -> tuple[set[Path], list[str]]:
    failures: list[str] = []
    paths: set[Path] = set()
    local: Path | None = None

    access_method = entry.get("access_method")

    raw_local = entry.get("local_file")
    if normalized(raw_local):
        local = case_evidence_path(case_dir, raw_local)
        if local is None:
            failures.append(f"local_file {raw_local!r} does not resolve to a case-local file")
        else:
            paths.add(local)
            lead_failure = rlm_lead_failure(local)
            if lead_failure:
                failures.append(lead_failure)

    if "source_ref" in entry:
        ref_paths, ref_failures = validate_source_ref(
            case_dir, entry.get("source_ref"), entry.get("quote")
        )
        paths.update(ref_paths)
        failures.extend(ref_failures)
        if local is not None and ref_paths and local not in ref_paths:
            failures.append("local_file and source_ref.path identify different artifacts")

    bundle_id = normalized(entry.get("evidence_bundle_id"))
    if bundle_id:
        bundle = bundles.get(bundle_id)
        if bundle is None:
            failures.append(f"evidence_bundle_id {bundle_id!r} does not resolve")
        elif not canonical_bundle:
            failures.append(
                "legacy evidence bundles cannot anchor a positive verdict; migrate the bundle "
                "or cite its case-local artifact with local_file/source_ref"
            )
        else:
            bundle_paths, bundle_failures = validate_bundle_item(
                case_dir,
                bundle,
                finding_id,
                finding_claim,
                canonical=canonical_bundle,
            )
            paths.update(bundle_paths)
            failures.extend(bundle_failures)

    quote = normalized(entry.get("quote"))
    if quote and "source_ref" not in entry:
        if not paths:
            failures.append("exact quote has no case-local file or source_ref")
        elif not any(quote in normalized(path.read_text(errors="replace")) for path in paths):
            failures.append("exact quote is not present in the referenced case-local file")

    expected_hash = normalized(entry.get("sha256")).lower()
    if expected_hash:
        if not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
            failures.append("sha256 must contain 64 hexadecimal characters")
        elif not paths:
            failures.append("sha256 has no case-local artifact to hash")
        elif not any(sha256_file(path) == expected_hash for path in paths):
            failures.append("sha256 does not match the referenced case-local artifact")

    if paths and not bundle_id:
        allowed = {"full_text", "open_access", "archive_copy"}
        if verdict == "partially_verified":
            allowed.add("abstract_only")
        if access_method not in allowed:
            failures.append(
                f"access_method must be one of {sorted(allowed)} for a {verdict} anchor"
            )
    return paths, failures


def validate(case_dir: Path) -> tuple[list[dict[str, Any]], int]:
    data_dir = case_dir / "data"
    structure = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "validate-case.py"), str(case_dir), "--fact-check-only"],
        capture_output=True,
        text=True,
    )
    if structure.returncode != 0:
        detail = structure.stderr.strip() or structure.stdout.strip()
        return [{"id": "-", "ok": False, "reason": f"fact-check structure invalid: {detail}"}], 3
    try:
        findings_doc = load_object(data_dir / "findings.json")
        fact_check_doc = load_object(data_dir / "fact-check.json")
    except ValueError as exc:
        return [{"id": "-", "ok": False, "reason": str(exc)}], 3

    bundle_doc: dict[str, Any] | None = None
    bundle_error: str | None = None
    bundle_path = data_dir / "evidence-bundle.json"
    if bundle_path.is_file():
        try:
            bundle_doc = load_object(bundle_path)
        except ValueError as exc:
            bundle_error = str(exc)
    bundles, bundle_index_failures = bundle_items(bundle_doc)
    canonical_bundle = bool(bundle_doc and isinstance(bundle_doc.get("items"), list))

    raw_findings = findings_doc.get("findings")
    if not isinstance(raw_findings, list) or not raw_findings:
        return [{"id": "-", "ok": False, "reason": "findings.json contains no findings"}], 3
    findings: dict[str, dict[str, Any]] = {}
    structural: list[str] = []
    for index, finding in enumerate(raw_findings):
        if not isinstance(finding, dict):
            structural.append(f"findings.json[{index}] is not an object")
            continue
        finding_id = normalized(finding.get("id"))
        if not finding_id:
            structural.append(f"findings.json[{index}] has no non-empty id")
            continue
        if finding_id in findings:
            structural.append(f"findings.json contains duplicate id {finding_id}")
        if not normalized(finding.get("claim")):
            structural.append(f"finding {finding_id} has no non-empty claim")
        findings[finding_id] = finding
    if structural:
        return [{"id": "-", "ok": False, "reason": reason} for reason in structural], 3

    checks = canonical_fact_checks(fact_check_doc)
    if not checks:
        return [{"id": "-", "ok": False,
                 "reason": "fact-check file contains no verdicts — nothing was validated"}], 3

    results: list[dict[str, Any]] = []
    failed = 0
    for check in checks:
        check_id = check.get("id", "?")
        finding_id = normalized(check.get("finding_id"))
        status = check.get("status")
        finding = findings.get(finding_id)
        failures: list[str] = []
        if status not in VERDICTS:
            failures.append(f"unknown fact-check status {status!r}")
        if finding is None:
            failures.append(f"finding_id={finding_id!r} does not resolve to a finding")
        if not normalized(check.get("claim")):
            failures.append("fact-check claim text is missing")
        elif (finding is not None and status in ANCHORED_VERDICTS
              and normalized(check.get("claim")) != normalized(finding.get("claim"))):
            failures.append("fact-check claim text does not exactly match the linked finding")
        if failures:
            failed += 1
            results.append({"id": check_id, "ok": False, "reason": "; ".join(failures)})
            continue

        if status not in ANCHORED_VERDICTS:
            results.append({"id": check_id, "ok": True,
                            "reason": f"status={status} (evidence anchor not required)"})
            continue

        assert finding is not None
        if bundle_error and finding.get("evidence_bundle_refs"):
            failures.append(bundle_error)
        failures.extend(bundle_index_failures)

        # All declared finding source paths must be real, but they do not satisfy the
        # independent fact-check anchor on their own.
        for source in finding.get("sources", []):
            if not isinstance(source, dict) or not normalized(source.get("local_file")):
                continue
            if case_evidence_path(case_dir, source.get("local_file")) is None:
                failures.append(
                    f"finding source {source.get('local_file')!r} does not resolve to a case-local file"
                )

        for evidence_id in finding.get("evidence_bundle_refs", []):
            normalized_id = normalized(evidence_id)
            item = bundles.get(normalized_id)
            if item is None:
                failures.append(f"evidence bundle ref {normalized_id!r} does not resolve")
                continue
            _, item_failures = validate_bundle_item(
                case_dir,
                item,
                finding_id,
                normalized(finding.get("claim")),
                canonical=canonical_bundle,
            )
            failures.extend(item_failures)

        evidence_for = check.get("evidence_for")
        if not isinstance(evidence_for, list):
            evidence_for = []
        fact_check_paths: set[Path] = set()
        for index, entry in enumerate(evidence_for):
            if not isinstance(entry, dict):
                failures.append(f"evidence_for[{index}] must be an object")
                continue
            entry_paths, entry_failures = validate_evidence_entry(
                case_dir,
                entry,
                finding_id,
                normalized(finding.get("claim")),
                bundles,
                canonical_bundle=canonical_bundle,
                verdict=status,
            )
            fact_check_paths.update(entry_paths)
            failures.extend(f"evidence_for[{index}]: {reason}" for reason in entry_failures)
        if not fact_check_paths:
            failures.append(
                "verified verdict has no case-local fact-check evidence anchor; add local_file, "
                "source_ref, or evidence_bundle_id to evidence_for"
            )

        ok = not failures
        failed += 0 if ok else 1
        results.append({
            "id": check_id,
            "ok": ok,
            "reason": "case-local evidence anchors validated" if ok else "; ".join(failures),
            "claim": finding.get("claim"),
        })

    assessed = {normalized(check.get("finding_id")) for check in checks if normalized(check.get("finding_id"))}
    for finding_id in sorted(set(findings) - assessed):
        failed += 1
        results.append({
            "id": f"missing:{finding_id}",
            "ok": False,
            "reason": f"finding {finding_id} has no fact-check verdict — every finding must be assessed",
        })
    return results, (3 if failed else 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", help="case directory containing data/ and research/")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results, code = validate(Path(args.case_dir))
    if args.json:
        print(json.dumps({"ok": code == 0, "verdicts": results}, indent=2, ensure_ascii=False))
    else:
        for result in results:
            print(f"{'PASS' if result['ok'] else 'FAIL'}  {result['id']}: {result['reason']}")
        outcome = "OK" if code == 0 else "FAILED — bounce to the fact-checker with the reasons above"
        print(f"\nfact-check evidence: {outcome}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
