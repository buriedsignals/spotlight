"""A small synthetic data/indicator-framework.json for the checks: two sites
sharing an analytics tag and a hosting address, with two open items."""
from __future__ import annotations

import copy


def document(finding_id: str = "F1") -> dict:
    return copy.deepcopy({
        "framework": "indicator-framework",
        "cycle": 1,
        "indicators": [
            {
                "id": "ind-1", "value": "G-EXAMPLE01", "found_at": ["https://a.example/ (page source)"],
                "current_rating": {"fidelity": "High", "specificity": "High", "quadrant": "Solid"},
                "pairing_status": "confirmed_theirs", "branch_id": "br-1", "element_of": None,
                "finding_ids": [finding_id],
            },
            {
                "id": "ind-2", "value": "203.0.113.7", "found_at": ["DNS A record of a.example and b.example"],
                "current_rating": {"fidelity": "High", "specificity": "Low", "quadrant": "Shared"},
                "pairing_status": "not_applicable", "branch_id": "br-2", "element_of": None,
                "finding_ids": [],
            },
        ],
        "branches": [
            {"branch_id": "br-1", "root_decision": "Analytics account", "description": "One tag on both sites",
             "indicator_ids": ["ind-1"], "strongest": ["ind-1"]},
            {"branch_id": "br-2", "root_decision": "Hosting", "description": "Same shared hosting address",
             "indicator_ids": ["ind-2"], "strongest": ["ind-2"]},
        ],
        "conclusions": [
            {"conclusion_id": "c-1", "finding_ids": [finding_id], "question": "connection",
             "statement": "Both sites share an operator", "branches_present": ["br-1", "br-2"]},
        ],
        "temporal_summary": "2026-03-02 b.example registered; 2026-04-10 tag seen on both sites",
        "indicator_gaps": [
            {"item_id": "t-1", "kind": "shared_carrying_weight", "indicator_ids": ["ind-2"], "conclusion_ids": ["c-1"],
             "open": "The shared hosting address is used by thousands of sites.",
             "suggested_step": "Treat it as context; look for a second independent decision.", "assign_to": None},
            {"item_id": "t-2", "kind": "temporal", "indicator_ids": ["ind-1"],
             "open": "The tag was collected after b.example changed owner.",
             "suggested_step": "Pull an archived copy from before the change.", "assign_to": None},
        ],
    })
