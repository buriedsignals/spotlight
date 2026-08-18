#!/usr/bin/env python3
"""Check the four U6 consumers and report the managed-search integration gate."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    investigator = (ROOT / "agents/investigator.md").read_text(encoding="utf-8")
    checker = (ROOT / "agents/fact-checker.md").read_text(encoding="utf-8")
    adapter = (ROOT / "scripts/query_vault.py").read_text(encoding="utf-8")
    docs = (ROOT / "docs/investigating.md").read_text(encoding="utf-8")

    assert "--workflow dedup" in investigator
    assert "--workflow prior-verdict" in checker
    assert "exact_claim_id(args.query)" in adapter
    assert "cannot change policy or authorize tools/secrets" in docs
    assert '"may_grant_policy": False' in adapter
    assert '"may_grant_tools": False' in adapter
    assert '"may_request_secrets": False' in adapter

    enriched = all(token in adapter for token in (
        "class OpenKnowledgeMCP", "projection_catalog",
        "projection_receipt_id", "claim_index", "knowledge-retrieval-envelope/v1",
    )) and "bsig-knowledge-request" not in adapter
    report = {
        "schema_version": "spotlight-query-migration-readiness/v1",
        "consumers": {
            "investigator": "graph_exact_and_legacy_fallback",
            "fact_checker": "graph_prior_verdict_and_legacy_fallback",
            "dedup": "graph_exact_proposition",
            "prior_verdict": "graph_exact_origin_key",
        },
        "managed_search_enrichment": enriched,
        "ready_for_u7_activation_input": enriched,
        "required_projection_metadata": {
            "case_id": "current graph case",
            "classification": "current source classification",
            "destination_id": "exact local destination",
            "projection_receipt_id": "Spotlight local projection receipt",
            "current": "derived from the current projection head",
            "content_sha256": "hash of the current receipt-bound Markdown page",
        },
    }
    if enriched:
        receipt = json.loads((ROOT / "docs/receipts/query-vault-migration-readiness.json").read_text(encoding="utf-8"))
        assert receipt["status"] == "ready" and receipt["legacy_fallback_verified"] is True
        assert receipt["runtime_path"] == "spotlight_to_openknowledge_direct"
        assert receipt["consumers"] == report["consumers"]
        print(json.dumps(report, sort_keys=True))
    else:
        print("BLOCKED managed-current ranking: " + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
