#!/usr/bin/env python3
"""Validate fact-check evidence through language-neutral, case-local anchors.

The validator deliberately does not tokenize, translate, or semantically grade prose.
For every finding, whatever its verdict, it proves that the evidence records
behind it hold together: every ``evidence_bundle_refs`` item resolves, links
back to the finding with the claim copied verbatim and a known support type,
and points at an intact case-local artifact. An honest record of a failed or
pending acquisition (a fallback that failed, a human-verification flag, an
insufficient or contradicting link) passes; lowering a verdict never hides a
broken record.

For every positive verdict (``verified``; legacy 1.0 files also
``partially_verified``) it also proves that:

* the fact-check claim exactly matches its linked finding after Unicode/whitespace
  normalization;
* at least one fact-check evidence item resolves to a file inside the case;
* declared line ranges, JSON Pointers, exact quotes, and SHA-256 values are valid;
* every evidence-bundle item the anchors rely on (cited by id, or owning a
  cited file) can anchor it: not flagged for human verification, any source
  fallback succeeded, and, when cited by id, linked to the finding as direct or
  indirect support. Other items of the finding may stay on record as they are.

Each failure names its owner. A bundle item belongs to the agent that acquired
it (``created_by``; the investigator unless it says ``fact-checker``), so its
record failures go to that agent. The fact-check's own anchors, and the choice
to rely on an item that cannot anchor the verdict, are the fact-checker's: it
cites another anchor or lowers the verdict; the record is never rewritten to fit.

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
    fallback_outstanding,
    normalized,
    rlm_lead_failure,
    sha256_file,
    validate_source_ref,
)


from verdicts import allowed_verdicts, fact_check_version, positive_verdicts
ARTIFACT_PATH_KEYS = ("raw_path", "downloaded_document_path", "screenshot_path", "path")
SUPPORT_TYPES = {"direct", "indirect", "inferred", "contradicted", "insufficient"}

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


def bundle_owner(item: dict[str, Any]) -> str:
    """The agent that acquired an evidence-bundle item and alone may correct it."""
    return "fact-checker" if item.get("created_by") == "fact-checker" else "investigator"


def bundle_record_failures(
    case_dir: Path,
    item: dict[str, Any],
    finding_id: str,
    finding_claim: str,
    *,
    canonical: bool,
    require_link: bool = True,
) -> tuple[set[Path], list[str]]:
    """Integrity of one bundle item as a record of what was acquired for a finding.

    Checked whatever the verdicts are. An honest record of a failed or
    pending acquisition passes: a failed fallback, a contradicting or
    insufficient link, or a human-verification flag is accurate history, not
    a defect. What fails is a record that does not hold together: no link
    back to the finding, a claim link that does not copy the claim, a
    malformed gate, or an artifact path or hash that does not resolve.
    """
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
    if not require_link:
        pass
    elif not linked:
        failures.append(f"evidence bundle {evidence_id} has no explicit finding link")
    elif finding_id not in linked:
        failures.append(f"evidence bundle {evidence_id} is not linked to finding {finding_id}")
    if canonical and not isinstance(claim_links, list):
        failures.append(f"evidence bundle {evidence_id} needs structured claim_links")
    for link in finding_links(item, finding_id):
        if normalized(link.get("claim_text")) != normalized(finding_claim):
            failures.append(
                f"evidence bundle {evidence_id} claim link does not exactly match finding {finding_id}"
            )
        if link.get("support_type") not in SUPPORT_TYPES:
            failures.append(
                f"evidence bundle {evidence_id} links finding {finding_id} with support_type "
                f"{link.get('support_type')!r}, not one of {sorted(SUPPORT_TYPES)}"
            )
    if canonical and not isinstance(item.get("human_verification_required"), bool):
        failures.append(f"evidence bundle {evidence_id} needs boolean human_verification_required")
    gate = item.get("missing_source_gate")
    if canonical and isinstance(gate, bool):
        failures.append(f"evidence bundle {evidence_id} has malformed missing_source_gate")
    if gate is not None and not isinstance(gate, (dict, bool)):
        failures.append(f"evidence bundle {evidence_id} has malformed missing_source_gate")
    if isinstance(gate, dict) and not isinstance(gate.get("fallback_required"), bool):
        failures.append(
            f"evidence bundle {evidence_id} missing_source_gate needs boolean fallback_required"
        )

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


def finding_links(item: dict[str, Any], finding_id: str) -> list[dict[str, Any]]:
    links = item.get("claim_links")
    return [
        link for link in links if isinstance(link, dict) and normalized(link.get("finding_id")) == finding_id
    ] if isinstance(links, list) else []


def source_ineligibility(item: dict[str, Any]) -> list[str]:
    """Why the source behind a bundle item cannot anchor a positive verdict."""
    reasons: list[str] = []
    if item.get("human_verification_required") is not False:
        reasons.append("is flagged for human verification")
    if fallback_outstanding(item.get("missing_source_gate")):
        reasons.append("records a source fallback that did not succeed")
    return reasons


def support_failures(item: dict[str, Any], finding_id: str, verdict: str) -> list[str]:
    """Why a bundle item the fact-check cites by id cannot anchor this verdict.

    These are the fact-checker's to resolve: cite another anchor or lower the
    verdict. The bundle record itself may be accurate, so the reason never
    asks for it to be changed.
    """
    evidence_id = normalized(item.get("id")) or "?"
    reasons = source_ineligibility(item)
    links = finding_links(item, finding_id)
    if not links:
        reasons.append(f"is not linked to finding {finding_id}")
    elif not any(link.get("support_type") in {"direct", "indirect"} for link in links):
        reasons.append(f"does not link finding {finding_id} as direct or indirect support")
    return [
        f"cites evidence bundle {evidence_id}, which {reason}; it cannot anchor a {verdict} "
        "verdict — cite another anchor or lower the verdict"
        for reason in reasons
    ]


def items_owning(case_dir: Path, bundles: dict[str, dict[str, Any]], paths: set[Path]) -> list[tuple[dict[str, Any], bool]]:
    """Bundle items whose artifact (or a derivative of it) is one of ``paths``.

    The flag is True when the path is a text derivative flagged for human
    verification.
    """
    owners: list[tuple[dict[str, Any], bool]] = []
    for item in bundles.values():
        owned = {
            path for key in ARTIFACT_PATH_KEYS
            if (path := case_evidence_path(case_dir, item.get(key))) is not None
        }
        flagged = False
        derivatives = item.get("text_derivatives")
        for derivative in derivatives if isinstance(derivatives, list) else []:
            if not isinstance(derivative, dict):
                continue
            path = case_evidence_path(case_dir, derivative.get("path"))
            if path is not None:
                owned.add(path)
                if path in paths and derivative.get("human_verification_required") is True:
                    flagged = True
        if owned & paths:
            owners.append((item, flagged))
    return owners


def validate_evidence_entry(
    case_dir: Path,
    entry: dict[str, Any],
    finding_id: str,
    finding_claim: str,
    bundles: dict[str, dict[str, Any]],
    *,
    canonical_bundle: bool,
    verdict: str,
) -> tuple[set[Path], list[str], dict[str, list[str]]]:
    """Check one evidence_for anchor of a positive verdict.

    Returns the anchored paths, the fact-checker's failures, and failures in
    the bundle records the anchor cites, keyed by the agent that owns them.
    """
    failures: list[str] = []
    records: dict[str, list[str]] = {}
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
            # Citing an item that is not linked to this finding is the
            # fact-checker's choice (support_failures reports it), not a
            # defect in the item's record.
            bundle_paths, record = bundle_record_failures(
                case_dir, bundle, finding_id, finding_claim, canonical=True, require_link=False,
            )
            paths.update(bundle_paths)
            records.setdefault(bundle_owner(bundle), []).extend(record)
            failures.extend(support_failures(bundle, finding_id, verdict))

    # An anchor citing a stored file by path relies on the source the bundle
    # recorded for that file, so that source must be able to anchor a
    # positive verdict too.
    for item, flagged_derivative in items_owning(case_dir, bundles, paths):
        evidence_id = normalized(item.get("id")) or "?"
        # An item cited by id already had its source checked above.
        reasons = [] if evidence_id == bundle_id else source_ineligibility(item)
        if flagged_derivative:
            reasons.append("has a text derivative flagged for human verification")
        failures.extend(
            f"relies on a file of evidence bundle {evidence_id}, which {reason}; it cannot anchor a "
            f"{verdict} verdict — cite another anchor or lower the verdict"
            for reason in reasons
        )

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
    return paths, failures, records


def validate(case_dir: Path) -> tuple[list[dict[str, Any]], int]:
    data_dir = case_dir / "data"
    structure = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "validate-case.py"), str(case_dir), "--fact-check-only"],
        capture_output=True,
        text=True,
    )
    if structure.returncode != 0:
        detail = structure.stderr.strip() or structure.stdout.strip()
        return [{"id": "-", "ok": False, "owner": "structure",
                 "reason": f"fact-check structure invalid: {detail}"}], 3
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
        # An explicit "nothing qualified" record (validated by validate-case.py
        # above) is an outcome, not a gap; there are no verdicts to anchor.
        nothing = findings_doc.get("no_qualifying_findings")
        if raw_findings == [] and isinstance(nothing, dict) and normalized(nothing.get("reason")):
            if canonical_fact_checks(fact_check_doc):
                return [{"id": "-", "ok": False,
                         "reason": "fact-check has verdicts but findings.json records that nothing qualified"}], 3
            return [{"id": "-", "ok": True,
                     "reason": "findings.json records that nothing qualified: " + normalized(nothing.get("reason"))}], 0
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

    # Contract 1.1 accepts only Batch 1.1's five verdicts; legacy 1.0 files
    # keep their recorded vocabulary.
    version = fact_check_version(fact_check_doc)
    verdicts = allowed_verdicts(version)
    anchored = positive_verdicts(version)
    results: list[dict[str, Any]] = []
    failed = 0

    # The evidence records behind every finding are checked whatever the
    # verdicts are, so lowering a verdict cannot hide a broken record.
    if bundle_error and any(f.get("evidence_bundle_refs") for f in findings.values()):
        bundle_index_failures = [bundle_error, *bundle_index_failures]
    if bundle_index_failures:
        failed += 1
        results.append({"id": "evidence-bundle", "ok": False, "owner": "investigator",
                        "reason": "; ".join(bundle_index_failures)})
    for finding_id, finding in findings.items():
        records: dict[str, list[str]] = {}
        for source in finding.get("sources", []) if isinstance(finding.get("sources"), list) else []:
            if not isinstance(source, dict) or not normalized(source.get("local_file")):
                continue
            if case_evidence_path(case_dir, source.get("local_file")) is None:
                records.setdefault("investigator", []).append(
                    f"finding source {source.get('local_file')!r} does not resolve to a case-local file"
                )
        refs = finding.get("evidence_bundle_refs")
        for evidence_id in refs if isinstance(refs, list) else []:
            normalized_id = normalized(evidence_id)
            item = bundles.get(normalized_id)
            if item is None:
                records.setdefault("investigator", []).append(
                    f"evidence bundle ref {normalized_id!r} does not resolve")
                continue
            _, item_failures = bundle_record_failures(
                case_dir, item, finding_id, normalized(finding.get("claim")), canonical=canonical_bundle,
            )
            records.setdefault(bundle_owner(item), []).extend(item_failures)
        for owner, reasons in records.items():
            if reasons:
                failed += 1
                results.append({"id": f"record:{finding_id}", "ok": False, "owner": owner,
                                "reason": "; ".join(reasons), "claim": finding.get("claim")})

    for check in checks:
        check_id = check.get("id", "?")
        finding_id = normalized(check.get("finding_id"))
        status = check.get("status")
        finding = findings.get(finding_id)
        failures: list[str] = []
        if status not in verdicts:
            failures.append(f"fact-check status {status!r} is not a contract {version} verdict ({', '.join(sorted(verdicts))})")
        if finding is None:
            failures.append(f"finding_id={finding_id!r} does not resolve to a finding")
        # A claim that is not its finding's claim fails the structure check
        # above (validate-case.py cross_reference), whatever the verdict.
        if not normalized(check.get("claim")):
            failures.append("fact-check claim text is missing")
        if failures:
            failed += 1
            results.append({"id": check_id, "ok": False, "owner": "fact-checker", "reason": "; ".join(failures)})
            continue

        if status not in anchored:
            results.append({"id": check_id, "ok": True,
                            "reason": f"status={status} (evidence anchor not required)"})
            continue

        assert finding is not None
        # Failures in the bundle records this verdict cites, by the agent
        # that acquired them: only that agent can correct its record.
        record_failures: dict[str, list[str]] = {}
        evidence_for = check.get("evidence_for")
        if not isinstance(evidence_for, list):
            evidence_for = []
        fact_check_paths: set[Path] = set()
        for index, entry in enumerate(evidence_for):
            if not isinstance(entry, dict):
                failures.append(f"evidence_for[{index}] must be an object")
                continue
            entry_paths, entry_failures, entry_records = validate_evidence_entry(
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
            for owner, reasons in entry_records.items():
                record_failures.setdefault(owner, []).extend(
                    f"evidence_for[{index}]: {reason}" for reason in reasons)
        if not fact_check_paths:
            failures.append(
                "verified verdict has no case-local fact-check evidence anchor; add local_file, "
                "source_ref, or evidence_bundle_id to evidence_for"
            )
        by_owner = {
            "investigator": record_failures.get("investigator", []),
            "fact-checker": failures + record_failures.get("fact-checker", []),
        }
        if not any(by_owner.values()):
            results.append({
                "id": check_id,
                "ok": True,
                "reason": "case-local evidence anchors validated",
                "claim": finding.get("claim"),
            })
            continue
        failed += 1
        for owner, reasons in by_owner.items():
            if reasons:
                results.append({
                    "id": check_id,
                    "ok": False,
                    "owner": owner,
                    "reason": "; ".join(reasons),
                    "claim": finding.get("claim"),
                })

    assessed = {normalized(check.get("finding_id")) for check in checks if normalized(check.get("finding_id"))}
    for finding_id in sorted(set(findings) - assessed):
        failed += 1
        results.append({
            "id": f"missing:{finding_id}",
            "ok": False,
            "owner": "fact-checker",
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
            owner = f" [{result['owner']}]" if result.get("owner") else ""
            print(f"{'PASS' if result['ok'] else 'FAIL'}  {result['id']}{owner}: {result['reason']}")
        outcome = "OK" if code == 0 else (
            "FAILED — [investigator] reasons go to the investigator (evidence correction), "
            "[fact-checker] reasons to the fact-checker, [structure] reasons to the structural correction"
        )
        print(f"\nfact-check evidence: {outcome}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
