"""Deterministic rules that turn decision-model answers into Spotlight signals.

Thresholds were fitted on the dev half of the evaluation sets and frozen before
the held-out half was scored (tools/decision-model-evidence/jev-2026-09-28/).
Signals only lower confidence or add flags; nothing here can raise confidence.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

GROUNDING_THRESHOLDS = {
    "support": 0.6, "p_direct": 0.5, "current": 0.3, "complete": 0.2, "legal": 0.6,
    "attribution": 0.6, "direction": 0.6, "control": 0.6, "stage": 0.6,
    "amounts": 0.6, "dates": 0.6, "scope": 0.5, "causation": 0.6, "arithmetic": 0.7,
    "claim_beneficial": 0.9,
}
ENTITY_AMBIGUITY = 0.6
PROPOSITION_PASS = {"equivalent", "narrower"}
LEGAL_WORDS = re.compile(
    r"legal|domicil|incorporat|registered (?:in|office|as|with)|\bseat\b|"
    r"siège|siege|domicili|immatricul|inscrit|constitu|"
    r"\bsitz\b|domizil|eingetragen|registriert|gegründet|"
    r"\bsede\b|iscritt|registrat|costituit|domiciliat|"
    r"domicilio social|domiciliad|constituid|registrad",
    re.I,
)
STAGE_RANK = {"proposed_or_requested": 1, "approved_or_decided": 2, "paid_or_executed": 3}
SUPPORT_CAP = {"direct": "none", "partial": "medium", "insufficient": "low", "contradicted": "low"}


def _top(answer: Mapping[str, Any]) -> tuple[str, float]:
    choice = answer.get("choice", "")
    return choice, float(answer.get("probabilities", {}).get(choice, 0.0))


def _agree(answers: Mapping[str, Any], qid: str, threshold: float = 0.0) -> str | None:
    """The choice both option orders agree on with probability >= threshold in each, else None."""
    first, second = answers[qid], answers[qid + "_rev"]
    choice = first.get("choice")
    if choice != second.get("choice"):
        return None
    if min(float(first["probabilities"].get(choice, 0.0)), float(second["probabilities"].get(choice, 0.0))) < threshold:
        return None
    return choice


def _both(answers: Mapping[str, Any], qid: str, value: str, threshold: float) -> bool:
    return _agree(answers, qid, threshold) == value


def grounding_signal(claim: str, answers: Mapping[str, Any], t: Mapping[str, float] = GROUNDING_THRESHOLDS) -> dict[str, Any]:
    """Return {status, route_reason?, support, support_probability, cap, flags, facets}."""
    facets = {
        "claim_type": answers["c_claim_type"]["choice"],
        "temporal_status": answers["c_temporal"]["choice"],
        "source_assertion": answers["g_attribution"]["choice"],
    }
    support, support_p = _top(answers["g_support"])
    base = {"support": support, "support_probability": round(support_p, 4), "facets": facets}
    if float(answers["g_arithmetic"]["noul"]) >= t["arithmetic"]:
        return {**base, "status": "routed", "route_reason": "arithmetic_required", "cap": "none",
                "flags": [{"reason": "arithmetic_required", "detail": "Sums, counts or conversions are checked by the existing fact-check process."}]}
    flags: list[dict[str, str]] = []

    def flag(reason: str, detail: str = "") -> None:
        flags.append({"reason": reason, **({"detail": detail} if detail else {})})

    if support != "direct" and support_p >= t["support"]:
        flag(f"support_{support}")
    mean_direct = (float(answers["g_support"]["probabilities"].get("direct", 0.0))
                   + float(answers["g_support_rev"]["probabilities"].get("direct", 0.0))) / 2
    if mean_direct < t["p_direct"] and not any(f["reason"].startswith("support_") for f in flags):
        flag("support_not_direct")
    # Only when the claim itself presents the situation as happened or current.
    if float(answers["g_current"]["noul"]) < t["current"] and answers["c_temporal"]["choice"] in ("current", "historical"):
        flag("announced_not_current")
    if float(answers["g_complete"]["noul"]) < t["complete"]:
        flag("incomplete_list")
    if _both(answers, "g_legal", "address_only", t["legal"]) and LEGAL_WORDS.search(claim):
        flag("address_as_legal_domicile")
    attribution, attribution_p = _top(answers["g_attribution"])
    if attribution == "reported_claim" and attribution_p >= t["attribution"] and float(answers["g_claim_attributed"]["noul"]) < 0.5:
        flag("allegation_as_fact")
    if _both(answers, "g_direction", "reversed_or_different", t["direction"]):
        flag("direction_mismatch")
    control, control_p = _top(answers["g_control"])
    claims_beneficial = float(answers["g_claim_beneficial"]["noul"]) >= t["claim_beneficial"]
    # Job titles and board seats are not ownership: only fire when the claim asserts ownership or control.
    claims_ownership = answers["c_claim_type"]["choice"] == "ownership_control" or claims_beneficial
    if control in ("role_only", "indirect_only") and control_p >= t["control"] and claims_ownership:
        flag("control_overstated", control)
    if control == "shareholding" and control_p >= t["control"] and claims_beneficial:
        flag("beneficial_owner_not_stated")
    stage_claim, pc = _top(answers["g_stage_claim"])
    stage_source, ps = _top(answers["g_stage_source"])
    if (stage_claim in STAGE_RANK and stage_source in STAGE_RANK
            and STAGE_RANK[stage_claim] > STAGE_RANK[stage_source] and min(pc, ps) >= t["stage"]):
        flag("stage_overstated", f"{stage_source} -> {stage_claim}")
    if _both(answers, "g_amounts", "mismatch", t["amounts"]):
        flag("amount_mismatch")
    if _both(answers, "g_dates", "mismatch", t["dates"]):
        flag("date_mismatch")
    if float(answers["g_scope"]["noul"]) >= t["scope"]:
        flag("scope_overreach")
    if float(answers["g_causation"]["noul"]) >= t["causation"]:
        flag("causation_added")

    if not flags:
        cap = "none"
    elif support in ("insufficient", "contradicted") and support_p >= t["support"]:
        cap = "low"
    elif any(f["reason"] in ("direction_mismatch", "amount_mismatch", "date_mismatch", "stage_overstated") for f in flags):
        cap = "low"
    else:
        cap = "medium"
    return {**base, "status": "judged", "cap": cap, "flags": flags}


def report_signal(verdict: str, answers: Mapping[str, Any]) -> dict[str, Any]:
    """Overstatement flags for one piece of report prose."""
    if verdict == "false" and float(answers["r_debunk"]["noul"]) >= 0.7:
        return {"status": "judged", "flags": [], "overstatement_type": "none"}
    fidelity = answers["r_fidelity"]
    type_answer = answers["r_type"]
    p_none = float(type_answer["probabilities"].get("none", 0.0))
    flags = []
    if fidelity["choice"] == "overstated" or (p_none < 0.3 and float(fidelity["probabilities"].get("overstated", 0.0)) >= 0.1):
        flags.append({"reason": "overstated"})
    elif _agree(answers, "r_fidelity") is None:
        flags.append({"reason": "fidelity_inconclusive", "detail": "the two option orders disagree"})
    if float(answers["r_scope"]["noul"]) >= 0.5:
        flags.append({"reason": "scope_widened"})
    if float(answers["r_actor"]["noul"]) >= 0.7:
        flags.append({"reason": "actor_changed"})
    kind = type_answer["choice"] if flags and type_answer["choice"] != "none" else ("unspecified" if flags else "none")
    return {"status": "judged", "flags": flags, "overstatement_type": kind}


def proposition_signal(answers: Mapping[str, Any]) -> dict[str, Any]:
    result = _agree(answers, "i_prop")
    if result is None:
        return {"status": "judged", "result": "inconclusive", "flags": [{"reason": "proposition_inconclusive", "detail": "the two option orders disagree"}]}
    flags = [] if result in PROPOSITION_PASS else [{"reason": f"proposition_{result}"}]
    return {"status": "judged", "result": result, "flags": flags}


def membership_signal(declared: str, answers: Mapping[str, Any]) -> dict[str, Any]:
    result = _agree(answers, "i_member")
    if result is None:
        return {"status": "judged", "result": "inconclusive", "declared": declared,
                "flags": [{"reason": "relation_inconclusive", "detail": "the two option orders disagree"}]}
    flags = [] if result == declared else [{"reason": "relation_disagrees", "detail": f"declared {declared}, model {result}"}]
    return {"status": "judged", "result": result, "declared": declared, "flags": flags}


def entity_signal(answers: Mapping[str, Any], declared: str = "") -> dict[str, Any]:
    result = "unclear" if float(answers["i_entity_ambiguous"]["noul"]) >= ENTITY_AMBIGUITY else answers["i_entity"]["choice"]
    flags = []
    if result == "unclear":
        flags.append({"reason": "entity_type_unclear"})
    elif declared and declared != result:
        flags.append({"reason": "entity_type_disagrees", "detail": f"declared {declared}, model {result}"})
    return {"status": "judged", "result": result, "declared": declared, "flags": flags}


def match_signal(answers: Mapping[str, Any]) -> dict[str, Any]:
    result = _agree(answers, "i_match")
    if result is None:
        return {"status": "judged", "result": "inconclusive", "flags": [{"reason": "match_inconclusive", "detail": "the two option orders disagree"}]}
    flags = [{"reason": f"existing_claim_{result}"}] if result in ("same_claim", "updates", "contradicts") else []
    return {"status": "judged", "result": result, "flags": flags}
