#!/usr/bin/env python3
"""Build the editorial-review artifact (``review.html``) from the case files.

Deterministic, stdlib-only, for every runtime: it joins ``data/findings.json``
with ``data/fact-check.json`` by ``finding_id``, adds the source expressions,
the provenance manifest and the Investigation-gate summary, serializes the
payload for an HTML script-data context, and injects it into
``skills/editorial-review/references/template.html``.

Nothing is invented here: every finding, verdict, expression and summary field
is copied from the case files; the headline and the verdict counts are derived
from them.

Usage:
    build-review-html.py CASE_DIR            # writes CASE_DIR/review.html
    build-review-html.py CASE_DIR --stdout   # prints the HTML instead
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spotlight_orchestration.case_writer import atomic_write_files
from spotlight_orchestration.contract import OrchestrationError


SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE = SCRIPT_DIR.parent / "skills/editorial-review/references/template.html"
MARKER = "/*INVESTIGATION_DATA*/"


class ReviewInputError(Exception):
    pass


def load_json(path: Path, *, required: bool) -> Any:
    if not path.is_file():
        if required:
            raise ReviewInputError(f"{path.name} is missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewInputError(f"{path.name} is not readable JSON: {exc}") from exc


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def build_summary(summary: Any, summary_md: str | None, findings: list[Any], cycles: Any) -> dict[str, Any]:
    """The gate's summary.json in either documented shape (schemas/summary.schema.json
    or the phase-investigation-gate example), falling back to summary.md."""
    summary = summary if isinstance(summary, dict) else {}
    verified = sum(1 for finding in findings if (finding.get("verdict") or {}).get("verdict") == "verified")
    cycle_text = f" across {cycles} cycle{'' if cycles == 1 else 's'}" if isinstance(cycles, int) and cycles > 0 else ""
    out: dict[str, Any] = {
        "headline": text_or_none(summary.get("title")) or f"{verified} verified finding{'' if verified == 1 else 's'}{cycle_text}",
        "overview": text_or_none(summary.get("overview")) or text_or_none(summary.get("summary")) or summary_md or "",
        "conclusions": as_list(summary.get("conclusions")) or as_list(summary.get("key_conclusions")),
        "limitations": as_list(summary.get("limitations")),
    }
    for key in ("scope", "confidence_assessment", "methodology_summary"):
        if text_or_none(summary.get(key)):
            out[key] = summary[key]
    return out


def build_payload(case_dir: Path, generated_at: str) -> dict[str, Any]:
    data = case_dir / "data"
    findings_doc = load_json(data / "findings.json", required=True)
    fact_check = load_json(data / "fact-check.json", required=True)
    if not isinstance(findings_doc, dict) or not isinstance(fact_check, dict):
        raise ReviewInputError("findings.json and fact-check.json must be JSON objects")
    expressions_doc = load_json(data / "source-expressions.json", required=False)
    summary = load_json(data / "summary.json", required=False)
    manifest = load_json(data / "provenance-manifest.json", required=False)
    summary_path = case_dir / "summary.md"
    summary_md = summary_path.read_text(encoding="utf-8", errors="replace") if summary_path.is_file() else None

    claims = [claim for claim in as_list(fact_check.get("claims")) if isinstance(claim, dict)]
    # One verdict per finding: the last claim recorded for it.
    verdicts = {claim["finding_id"]: claim for claim in claims if isinstance(claim.get("finding_id"), str)}

    findings: list[dict[str, Any]] = []
    for finding in as_list(findings_doc.get("findings")):
        if not isinstance(finding, dict):
            continue
        entry = dict(finding)
        if isinstance(entry.get("evidence"), list):
            entry["evidence"] = "\n\n".join(str(item) for item in entry["evidence"])
        entry["verdict"] = verdicts.get(finding.get("id"))
        findings.append(entry)

    counts = Counter(claim.get("verdict") for claim in claims if isinstance(claim.get("verdict"), str))
    fact_check_summary = {"total_claims": len(claims)}
    for verdict in ("verified", "unverified", "disputed", "false"):
        fact_check_summary[verdict] = counts.pop(verdict, 0)
    fact_check_summary.update(sorted(counts.items()))

    cycles = summary.get("cycles") if isinstance(summary, dict) else None
    if not isinstance(cycles, int):
        cycles = max(
            (value for value in (findings_doc.get("cycle"), fact_check.get("cycle")) if isinstance(value, int)),
            default=None,
        )

    return {
        "project": findings_doc.get("project"),
        "generated_at": generated_at,
        "summary": build_summary(summary, summary_md, findings, cycles),
        "findings": findings,
        "source_expressions": as_list(expressions_doc.get("expressions")) if isinstance(expressions_doc, dict) else [],
        "provenance_manifest": manifest if isinstance(manifest, dict) else None,
        "fact_check_summary": fact_check_summary,
        "cycles": cycles,
        "existing_feedback": None,
    }


def script_safe_json(payload: dict[str, Any]) -> str:
    """JSON that cannot end its <script type="application/json"> element."""
    return (
        json.dumps(payload, ensure_ascii=False, indent=2)
        .replace("<", "\\u003c")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def render(case_dir: Path, generated_at: str) -> bytes:
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(MARKER) != 1:
        raise ReviewInputError(f"{TEMPLATE.name} must contain exactly one {MARKER} marker")
    return template.replace(MARKER, script_safe_json(build_payload(case_dir, generated_at)), 1).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--stdout", action="store_true", help="print the HTML instead of writing review.html")
    parser.add_argument("--generated-at", help="ISO 8601 timestamp to record (default: now, UTC)")
    args = parser.parse_args(argv)
    case_dir = args.case_dir.resolve()
    generated_at = args.generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        html = render(case_dir, generated_at)
        if args.stdout:
            sys.stdout.buffer.write(html)
            return 0
        atomic_write_files(case_dir, {"review.html": html})
    except (ReviewInputError, OrchestrationError, OSError) as exc:
        print(f"build-review-html: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"written": "review.html", "sha256": hashlib.sha256(html).hexdigest(), "bytes": len(html)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
