#!/usr/bin/env python3
"""Run a dummy Spotlight flow with and without RLM.

This is not a replacement for human agent dogfooding. It is a repeatable
flow-level proxy for the failure mode RLM is meant to reduce: a downstream
investigator working from a bounded context window misses late-but-relevant
source material, while an RLM artifact points it at source-linked leads.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_rlm import MODEL, lite_extract, read_corpus, run, validate_analysis  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def prepare_case(fixture: Path, labels: dict[str, Any]) -> Path:
    project = labels["project"]
    case_dir = ROOT / "cases" / project
    if case_dir.exists():
        shutil.rmtree(case_dir)
    research_dir = case_dir / "research"
    data_dir = case_dir / "data"
    research_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    for source in fixture.joinpath("corpus").iterdir():
        if source.is_file():
            shutil.copy2(source, research_dir / source.name)
    (data_dir / "methodology.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "project": project,
                "lead": labels["lead"],
                "planned_at": utc_now(),
                "brief_directions": ["inspect local corpus for procurement leads"],
                "skills_invoked": ["integrations", "investigation-methodology", "epistemic-grounding"],
                "navigator": {"required": False, "used": False, "fallback_used": True, "fallback_reason": "local dummy fixture"},
                "investigation_plan": [
                    {
                        "direction": "local corpus review",
                        "questions": ["Which source-linked leads are present?"],
                        "steps": [
                            {
                                "order": 1,
                                "action": "Read local research files and extract source-linked leads.",
                                "tool": "read-file",
                                "target": "cases/{project}/research/",
                                "expected_evidence": "verbatim source lines",
                                "fallback": "Use RLM artifact source_refs when enabled.",
                            }
                        ],
                    }
                ],
                "tools_required": ["read-file"],
                "opsec_considerations": [],
                "limitations": ["synthetic fixture; no external acquisition"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return case_dir


def artifact_texts(artifacts: list[dict[str, Any]]) -> list[str]:
    return [str(artifact.get("text", "")) for artifact in artifacts]


def match_expected(texts: list[str], labels: dict[str, Any]) -> list[str]:
    matched: list[str] = []
    haystack = "\n".join(texts)
    for item in labels["expected_artifacts"]:
        expected = item["text"]
        if expected in haystack:
            matched.append(expected)
    return matched


def match_absent(texts: list[str], labels: dict[str, Any]) -> list[str]:
    matched: list[str] = []
    haystack = "\n".join(texts)
    for absent in labels.get("expected_absent", []):
        if absent in haystack:
            matched.append(absent)
    return matched


def has_expected_contradiction(artifacts: list[dict[str, Any]], labels: dict[str, Any]) -> bool:
    contradiction_terms = [term.lower() for term in labels.get("expected_contradiction_terms", [])]
    if not contradiction_terms:
        return True
    for artifact in artifacts:
        text = str(artifact.get("text", "")).lower()
        if artifact.get("kind") == "contradiction" and all(term in text for term in contradiction_terms):
            return True
    return False


def build_findings(project: str, mode: str, artifacts: list[dict[str, Any]], source_lines_read: set[tuple[str, int]]) -> dict[str, Any]:
    findings = []
    for index, artifact in enumerate(artifacts, start=1):
        if artifact.get("kind") == "discarded":
            continue
        refs = artifact.get("source_refs") or []
        source = refs[0] if refs else {"path": "", "line_start": 1, "line_end": 1}
        findings.append(
            {
                "id": f"F{index}",
                "claim": str(artifact.get("text", "")),
                "evidence": str(artifact.get("text", "")),
                "sources": [
                    {
                        "url": f"file://{source.get('path', '')}",
                        "type": "file",
                        "accessed": utc_now(),
                        "access_method": "full_text",
                        "local_file": f"cases/{project}/{source.get('path', '')}",
                    }
                ],
                "confidence": "low",
                "confidence_rationale": f"{mode} dummy flow; source-linked lead only, not human verified.",
                "grounding": {
                    "support_type": "direct",
                    "source_role": "primary",
                    "claim_elements_supported": ["source-linked text"],
                    "missing_assumptions": ["synthetic fixture"],
                    "confidence_cap": "low",
                    "misgrounding_risk": "low for source extraction; not a real-world verification.",
                    "grounding_rationale": "The claim text is copied from or points to a cited source line.",
                },
                "evidence_bundle_refs": [],
                "perspective": "official",
            }
        )
    return {
        "schema_version": "1.0",
        "project": project,
        "lead": f"dummy flow {mode}",
        "investigated_at": utc_now(),
        "cycle": 1,
        "questions": ["Which source-linked leads are present?"],
        "findings": findings,
        "connections": [],
        "gaps": [],
        "next_steps": [f"source_lines_read={len(source_lines_read)}"],
    }


def score_mode(
    *,
    mode: str,
    artifacts: list[dict[str, Any]],
    labels: dict[str, Any],
    source_lines_read: set[tuple[str, int]],
    started: float,
) -> dict[str, Any]:
    texts = artifact_texts(artifacts)
    matched = match_expected(texts, labels)
    absent_hits = match_absent(texts, labels)
    expected_count = len(labels["expected_artifacts"])
    expected_plus_absent = len(matched) + len(absent_hits)
    return {
        "mode": mode,
        "status": "ok",
        "model": MODEL if mode.startswith("with_rlm") else None,
        "wall_time_seconds": round(time.time() - started, 3),
        "artifact_count": len(artifacts),
        "expected_matched": matched,
        "expected_total": expected_count,
        "finding_recall": round(len(matched) / expected_count, 3) if expected_count else 1.0,
        "absent_hits": absent_hits,
        "absent_hit_count": len(absent_hits),
        "lead_precision": round(len(matched) / expected_plus_absent, 3) if expected_plus_absent else 1.0,
        "contradiction_required": bool(labels.get("expected_contradiction_terms", [])),
        "contradiction_requirement_satisfied": has_expected_contradiction(artifacts, labels),
        "source_lines_read_by_downstream": len(source_lines_read),
    }


def score_error(*, mode: str, started: float, exc: Exception) -> dict[str, Any]:
    return {
        "mode": mode,
        "status": "error",
        "model": MODEL if mode.startswith("with_rlm") else None,
        "wall_time_seconds": round(time.time() - started, 3),
        "artifact_count": 0,
        "expected_matched": [],
        "expected_total": 0,
        "finding_recall": 0.0,
        "absent_hits": [],
        "absent_hit_count": 0,
        "lead_precision": 0.0,
        "contradiction_required": False,
        "contradiction_requirement_satisfied": False,
        "source_lines_read_by_downstream": 0,
        "error": str(exc),
    }


def run_no_rlm(labels: dict[str, Any], *, chunk_budget: int) -> tuple[list[dict[str, Any]], set[tuple[str, int]]]:
    chunks = read_corpus(labels["project"], labels["corpus_paths"])
    selected = chunks[:chunk_budget]
    return lite_extract(selected), {(chunk["path"], chunk["line"]) for chunk in selected}


def run_with_rlm(
    labels: dict[str, Any],
    *,
    prefilter: bool,
    hybrid: bool,
) -> tuple[list[dict[str, Any]], set[tuple[str, int]], dict[str, Any], dict[str, Any]]:
    request_path = ROOT / "cases" / labels["project"] / "data" / ".rlm-flow-request.json"
    request_path.write_text(
        json.dumps(
            {
                "project": labels["project"],
                "run_id": "flow-hybrid" if hybrid else "flow-full",
                "mode": "local_gemma4_e4b",
                "corpus_paths": labels["corpus_paths"],
                "prefilter": prefilter,
                "hybrid": hybrid,
            }
        ),
        encoding="utf-8",
    )
    result = run(request_path)
    request_path.unlink(missing_ok=True)
    analysis = json.loads(Path(result["analysis_path"]).read_text(encoding="utf-8"))
    errors = validate_analysis(analysis)
    if errors:
        raise RuntimeError("; ".join(errors))
    source_lines = {
        (ref["path"], line)
        for artifact in analysis["artifacts"]
        for ref in artifact.get("source_refs", [])
        for line in range(ref["line_start"], ref["line_end"] + 1)
    }
    return analysis["artifacts"], source_lines, result, analysis.get("metrics", {})


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark a dummy Spotlight flow with and without RLM.")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--no-rlm-chunk-budget", type=int, default=12)
    parser.add_argument("--keep-case", action="store_true")
    args = parser.parse_args()

    fixture = Path(args.fixture)
    labels = json.loads(fixture.joinpath("labels.json").read_text(encoding="utf-8"))
    case_dir = prepare_case(fixture, labels)

    results: list[dict[str, Any]] = []
    try:
        started = time.time()
        no_rlm_artifacts, no_rlm_lines = run_no_rlm(labels, chunk_budget=args.no_rlm_chunk_budget)
        results.append(
            score_mode(
                mode="without_rlm",
                artifacts=no_rlm_artifacts,
                labels=labels,
                source_lines_read=no_rlm_lines,
                started=started,
            )
        )
        case_dir.joinpath("data", "findings-without-rlm.json").write_text(
            json.dumps(build_findings(labels["project"], "without_rlm", no_rlm_artifacts, no_rlm_lines), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

        for flow_mode, prefilter, hybrid in (("with_rlm_full", False, False), ("with_rlm_hybrid_prefiltered", True, True)):
            started = time.time()
            try:
                rlm_artifacts, rlm_lines, rlm_result, rlm_metrics = run_with_rlm(labels, prefilter=prefilter, hybrid=hybrid)
                with_result = score_mode(
                    mode=flow_mode,
                    artifacts=rlm_artifacts,
                    labels=labels,
                    source_lines_read=rlm_lines,
                    started=started,
                )
                with_result["rlm_result"] = rlm_result
                with_result["rlm_metrics"] = rlm_metrics
                results.append(with_result)
                case_dir.joinpath("data", f"findings-{flow_mode.replace('_', '-')}.json").write_text(
                    json.dumps(build_findings(labels["project"], flow_mode, rlm_artifacts, rlm_lines), indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
            except Exception as exc:
                results.append(score_error(mode=flow_mode, started=started, exc=exc))

        result_by_mode = {result["mode"]: result for result in results}
        without = result_by_mode["without_rlm"]
        full = result_by_mode["with_rlm_full"]
        prefiltered = result_by_mode["with_rlm_hybrid_prefiltered"]
        output = {
            "created_at": utc_now(),
            "fixture": fixture.name,
            "project": labels["project"],
            "corpus_paths": labels["corpus_paths"],
            "no_rlm_chunk_budget": args.no_rlm_chunk_budget,
            "results": results,
            "delta": {
                "full_vs_without_recall": round(full["finding_recall"] - without["finding_recall"], 3),
                "prefiltered_vs_without_recall": round(prefiltered["finding_recall"] - without["finding_recall"], 3),
                "prefiltered_vs_full_seconds": round(prefiltered["wall_time_seconds"] - full["wall_time_seconds"], 3),
                "prefiltered_vs_full_source_lines": prefiltered["source_lines_read_by_downstream"]
                - full["source_lines_read_by_downstream"],
            },
        }
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": "ok", "out": str(out_path), "results": results}, indent=2, sort_keys=True))
        return 0
    finally:
        if not args.keep_case:
            shutil.rmtree(case_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
