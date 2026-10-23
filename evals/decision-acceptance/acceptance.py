#!/usr/bin/env python3
"""Decision-model acceptance test for research triage.

    python3 evals/decision-acceptance/acceptance.py --split dev --contender rules [--contender jev ...]
        [--fixture PATH ...] [--out RESULTS.json] [--private-detail DIR] [--write-acceptance]

Runs the research questions (integrations/decisions/research_questions.py)
through the shipped triage path (scripts/research-triage.py ``run_triage``),
the clue rules and the page ranking (scripts/research-merge.py) on fixture
cases, once per contender, and reports:

- lost leads: leads whose gold label says keep that triage would set aside.
  A lead kept as circumstantial (about another subject or event) is not lost:
  it stays on the page, and the count of such keeps is reported beside;
- research-visible failures caught (fact-checked claims that failed, labelled
  as visible before Investigation), reported apart from the measures on real leads;
- clue recall and false clues per 100 leads (planted clues, synthetic cases);
- per question: agreement with gold, both-order agreement, calibration;
- cost, latency and egress.

Triage runs as if the acceptance test had passed (full mode, the trial
thresholds from targets.json), because that is what the test must measure:
what the model would set aside once allowed to. Production stays flag-only
until ``integrations/decisions/research-acceptance.json`` exists for the bank
version, and only ``--write-acceptance`` after a passing held-out run writes it.

Fixtures: ``company-a.json`` (synthetic, in the repo) and every path in
``--fixture`` or ``$SPOTLIGHT_ACCEPTANCE_FIXTURES`` (os.pathsep-separated),
which is where the private fixture built from fact-checked claims lives. Item
detail of a non-synthetic fixture is written only with ``--private-detail``,
to a directory outside this checkout.

The held-out split runs only when targets.json is approved (status
"approved", approved_by set) for the current bank version: targets are written
before the held-out run, and the results record the targets file's sha256.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _path in (ROOT / "scripts", ROOT / "integrations" / "decisions", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import client  # noqa: E402
import research_backends as backends  # noqa: E402
import research_questions as rq  # noqa: E402
import research_rules as rr  # noqa: E402
import schema_check  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


triage = _load("spotlight_research_triage", ROOT / "scripts" / "research-triage.py")
merge = _load("spotlight_research_merge", ROOT / "scripts" / "research-merge.py")

FIXTURE_SCHEMA = HERE / "fixture.schema.json"
COMPANY_A = HERE / "company-a.json"
TARGETS = HERE / "targets.json"
FIXTURES_ENV = "SPOTLIGHT_ACCEPTANCE_FIXTURES"
CONTENDERS = ("rules", "jev", "clef", "clef-flash", "local")
CHOICE_QS = ("q1_identity", "q2_direction", "q3_source", "q4_assertion", "q5_status", "q6_fidelity", "c1_premise")
NOUL_QS = ("c2_new_actor", "c3_change")
SWEEP = (0.5, 0.6, 0.7, 0.8, 0.9)
# Which signals catch each failure class. A signal is "qid=value" for an answer
# both orders agreed on, "N1"/"S1" for the rules, or "clue:<group>" on the page.
CATCH = {
    "weak_source_chain": {"q3_source=secondary", "q3_source=interested_party", "q3_source=user_generated",
                          "q3_source=unclear", "q6_fidelity=unsupported", "S1"},
    "summary_wider": {"q6_fidelity=widens", "q6_fidelity=contradicts", "q6_fidelity=unsupported", "N1"},
    "inference_as_fact": {"q4_assertion=interpretation", "q6_fidelity=widens", "q6_fidelity=unsupported"},
    "conflicting_values": {"q6_fidelity=contradicts", "c1_premise=contradicts", "clue:conflict"},
    "wrong_subject": {"q1_identity=name_only", "q1_identity=different_subject_or_event", "q1_identity=none_of_the_brief"},
    "weak_relevance": {"q2_direction=none", "q1_identity=none_of_the_brief", "c1_premise=unrelated"},
    "allegation": {"q4_assertion=reported_claim"},
}
RULE_SIGNALS = {"N1", "S1"}
CLUE_QUESTION = {"premise": "c1_premise", "new_actor": "c2_new_actor", "change_or_absence": "c3_change",
                 "conflict": "c4_conflict"}


class AcceptanceError(Exception):
    """The run cannot go ahead as asked."""


# ------------------------------------------------------------------ inputs
def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_fixture(path: Path) -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    problems = schema_check.load(FIXTURE_SCHEMA).errors(fixture)
    ids: dict[str, str] = {}
    for item in fixture.get("items", []) if not problems else []:
        problems += [f"{item['item_id']}: {e}" for e in triage.schema_errors(triage.LEAD_SCHEMA, item["lead"])[:2]]
        if item["lead"].get("lead_id") != item["item_id"]:
            problems.append(f"{item['item_id']}: item_id must equal the lead's lead_id")
        if item["item_id"] in ids:
            problems.append(f"{item['item_id']}: duplicate item")
        ids[item["item_id"]] = item["split"]
    for clue in fixture.get("planted_clues", []) if not problems else []:
        for lead_id in clue["lead_ids"]:
            if ids.get(lead_id) != clue["split"]:
                problems.append(f"{clue['clue_id']}: {lead_id} is not a {clue['split']} item")
    if problems:
        raise AcceptanceError(f"{path.name} is not an acceptance fixture: {problems[:5]}")
    return fixture


def fixture_paths(extra: Iterable[str]) -> list[Path]:
    paths = [COMPANY_A]
    env = os.environ.get(FIXTURES_ENV, "")
    for raw in [*extra, *[p for p in env.split(os.pathsep) if p]]:
        path = Path(raw).expanduser().resolve()
        if path not in paths:
            paths.append(path)
    return paths


def load_targets(path: Path = TARGETS) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require_heldout_allowed(targets: Mapping[str, Any]) -> None:
    """Held-out runs only against targets Tom approved for this bank version."""
    if targets.get("status") != "approved" or not targets.get("approved_by"):
        raise AcceptanceError("the held-out half runs only after targets.json is approved (status 'approved', approved_by set)")
    if targets.get("bank_version") != rq.RESEARCH_BANK_VERSION:
        raise AcceptanceError("targets.json was written for another bank version; write and approve targets for this one")


def trial_acceptance(targets: Mapping[str, Any]) -> dict[str, Any]:
    return {"passed": True, "bank_version": rq.RESEARCH_BANK_VERSION, "thresholds": dict(targets["thresholds"]), "cut": []}


def case_documents(fixture: Mapping[str, Any], items: list[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The approved methodology and merged leads a real case would have at triage."""
    case = fixture["case"]
    questions = case.get("direction_questions") or {}
    methodology = {
        "lead": case["premise"],
        "investigation_plan": [{"direction_id": d, "direction": label, "questions": questions.get(d, [])}
                               for d, label in case["directions"].items()],
        "research_plan": {"topics": [dict(t) for t in case["topics"]],
                          **({"timing_window_days": case["timing_window_days"]} if case.get("timing_window_days") else {})},
    }
    leads_doc = {"generated_at": "2026-10-23T00:00:00Z", "leads": [{"lead": item["lead"]} for item in items]}
    return methodology, leads_doc


# ------------------------------------------------------------------ providers
class Metered:
    """Wraps a provider: keeps every request's state, raw answers, latency, cost and bytes sent."""

    def __init__(self, provider: Any, host: str) -> None:
        self._provider = provider
        self.model = getattr(provider, "model", "?")
        self.host = host
        self.calls: list[dict[str, Any]] = []
        self.failures = 0

    def decide(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        sent = len(json.dumps({"state": state, "questions": dict(questions)}, ensure_ascii=False).encode("utf-8"))
        started = time.monotonic()
        try:
            out = self._provider.decide(state, questions)
        except client.DecisionUnavailable:
            self.failures += 1
            raise
        usage = out.get("usage") or {}
        cost = usage.get("cost")
        self.calls.append({"state": state, "answers": out["answers"], "bytes_out": sent,
                           "latency_ms": out.get("latency_ms") or round((time.monotonic() - started) * 1000),
                           "cost": float(cost) if isinstance(cost, (int, float)) else None})
        return out


def build_provider(contender: str, args: argparse.Namespace) -> tuple[Any | None, str]:
    """(provider, egress host) for a contender; rules has none. Raises AcceptanceError when not configured."""
    if contender == "rules":
        return None, ""
    try:
        if contender == "jev":
            key = os.environ.get(client.KEY_ENV, "")
            if args.openrouter_key_file:
                key = Path(args.openrouter_key_file).expanduser().read_text(encoding="utf-8").strip().split()[-1]
            return client.JevOpenRouterProvider(key, model=args.jev_model), urlparse(client.DECISIONS_URL).hostname or ""
        if contender in ("clef", "clef-flash"):
            provider = backends.ClefWorkersAIProvider(os.environ.get(backends.CLEF_ACCOUNT_ENV, ""),
                                                      os.environ.get(backends.CLEF_TOKEN_ENV, ""), model=contender)
            return provider, "api.cloudflare.com"
        if contender == "local":
            return backends.LlamaServerProvider(args.local_url or ""), "loopback"
    except (client.DecisionUnavailable, OSError, IndexError) as exc:
        raise AcceptanceError(f"{contender} is not configured: {exc}") from None
    raise AcceptanceError(f"unknown contender {contender!r}")


# ------------------------------------------------------------------ one run
def _state_key(state: Any) -> str:
    return json.dumps((state or {}).get("item"), sort_keys=True, ensure_ascii=False)


def run_case(fixture: Mapping[str, Any], split: str, contender: str, provider: Any | None,
             acceptance: Mapping[str, Any]) -> dict[str, Any]:
    """Triage one fixture's split with one contender; returns per-lead detail."""
    items = [item for item in fixture["items"] if item["split"] == split]
    methodology, leads_doc = case_documents(fixture, items)
    clues_doc = {"clues": merge.clue_rules(leads_doc, methodology)}
    brief = triage.brief_from_methodology(methodology)
    cloud = contender in backends.CLOUD

    def routing(journalist_input: bool) -> tuple[str, str]:
        if journalist_input and cloud:
            return "rules", "journalist input is triaged only on this computer"
        return contender, ""

    document = triage.run_triage([item["lead"] for item in items], brief, clues_doc,
                                 lambda backend: provider if backend == contender else None, routing, acceptance)
    violations = rr.triage_violations(document)
    ranked = {r["lead_id"]: r for r in merge.rank(leads_doc, clues_doc, document)}
    rule_groups: dict[str, set[str]] = {}
    for clue in clues_doc["clues"]:
        for lead_id in clue["lead_ids"]:
            rule_groups.setdefault(lead_id, set()).add(merge.CLUE_RANK_GROUP[clue["type"]])
    raw = {}
    for call in getattr(provider, "calls", []):
        raw.setdefault(_state_key(call["state"]), call["answers"])
    triaged = {r["lead_id"]: r for r in document["leads"]}
    conflicts = document["conflicts"]
    detail = []
    for item in items:
        lead = item["lead"]
        result = triaged.get(item["item_id"])
        answers = {}
        if result is not None:
            answers = raw.get(_state_key(rr.lead_state(rr.parse_lead(lead))), {})
        detail.append({"item_id": item["item_id"], "gold": item["gold"], "absence": lead.get("absence", False),
                       "result": result, "raw": answers, "page_clues": sorted(ranked.get(item["item_id"], {}).get("clues", [])),
                       "rule_clues": sorted(rule_groups.get(item["item_id"], set()))})
    return {"fixture": fixture["name"], "synthetic": fixture["synthetic"], "items": detail,
            "planted": [c for c in fixture["planted_clues"] if c["split"] == split], "conflicts": conflicts,
            "violations": violations, "invalid_leads": document["invalid_leads"]}


# ------------------------------------------------------------------ measures
def signals(entry: Mapping[str, Any]) -> set[str]:
    result = entry["result"] or {}
    out = {f"{qid}={a['value']}" for qid, a in (result.get("answers") or {}).items()
           if a.get("counted") and isinstance(a.get("value"), str)}
    reasons = {f["reason"] for f in result.get("flags", [])}
    if "summary_refused" in reasons:
        out.add("N1")
    if "snippet_only" in reasons:
        out.add("S1")
    out |= {f"clue:{g}" for g in entry["page_clues"]}
    return out


def lost_leads(runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Keep-labelled leads triage set aside. A circumstantial keep is shown on
    the page, so it is counted apart and never as lost."""
    lost, kept, circumstantial = [], 0, 0
    for run in runs:
        for entry in run["items"]:
            if entry["gold"].get("keep") is True:
                kept += 1
                result = entry["result"] or {}
                if result.get("disposition") == "set_aside":
                    lost.append(entry["item_id"] if run["synthetic"] else "private")
                elif rr.CIRCUMSTANTIAL in (result.get("context") or []):
                    circumstantial += 1
    return {"lost": len(lost), "keep_items": kept, "lost_ids": [i for i in lost if i != "private"],
            "circumstantial_kept": circumstantial}


def failures_caught(runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Research-visible failures caught, by class; plus an unlabelled proxy."""
    by_class: dict[str, dict[str, int]] = {}
    unlabelled = visible = caught = 0
    proxy = {"failures": 0, "failures_flagged": 0, "verified": 0, "verified_flagged": 0}
    substantive = set().union(*CATCH.values())
    for run in runs:
        for entry in run["items"]:
            gold, found = entry["gold"], signals(entry)
            flagged = bool(found & substantive)
            if gold.get("verdict") == "verified":
                proxy["verified"] += 1
                proxy["verified_flagged"] += flagged
            failure = gold.get("failure")
            if not failure:
                continue
            proxy["failures"] += 1
            proxy["failures_flagged"] += flagged
            if failure.get("research_visible") is None or failure.get("class") is None:
                unlabelled += 1
                continue
            if not failure["research_visible"]:
                continue
            visible += 1
            row = by_class.setdefault(failure["class"], {"visible": 0, "caught": 0})
            row["visible"] += 1
            if found & CATCH[failure["class"]]:
                row["caught"] += 1
                caught += 1
    return {"research_visible": visible, "caught": caught, "rate": _rate(caught, visible), "by_class": by_class,
            "unlabelled_failures": unlabelled,
            "proxy_unlabelled": {**proxy, "failure_flag_rate": _rate(proxy["failures_flagged"], proxy["failures"]),
                                 "verified_flag_rate": _rate(proxy["verified_flagged"], proxy["verified"])}}


def clue_measures(runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    planted_total = found_total = false_total = leads_total = 0
    by_group: dict[str, dict[str, int]] = {}
    by_question: dict[str, dict[str, int]] = {}
    missed = []
    for run in [r for r in runs if r["planted"]]:
        page = {e["item_id"]: set(e["page_clues"]) for e in run["items"]}
        expected: dict[str, set[str]] = {}
        for clue in run["planted"]:
            planted_total += 1
            hit = any(clue["group"] in page.get(i, set()) for i in clue["lead_ids"])
            found_total += hit
            row = by_group.setdefault(clue["group"], {"planted": 0, "found": 0})
            row["planted"] += 1
            row["found"] += hit
            if clue.get("question"):
                q = by_question.setdefault(clue["question"], {"planted": 0, "found": 0})
                q["planted"] += 1
                q["found"] += hit
            if not hit:
                missed.append(clue["clue_id"])
            for lead_id in clue["lead_ids"]:
                expected.setdefault(lead_id, set()).add(clue["group"])
        for entry in run["items"]:
            leads_total += 1
            allowed = expected.get(entry["item_id"], set()) | set(entry["gold"].get("acceptable_clues", []))
            gold = entry["gold"].get("answers", {})
            # A clue from a question left unlabelled on this lead (ambiguous by design) is not scored.
            allowed |= {group for group, qid in CLUE_QUESTION.items() if qid != "c4_conflict" and qid not in gold}
            false_total += len(page[entry["item_id"]] - allowed)
    return {"planted": planted_total, "found": found_total, "recall": _rate(found_total, planted_total),
            "false_clues": false_total, "leads": leads_total,
            "false_clues_per_100_leads": round(100 * false_total / leads_total, 2) if leads_total else None,
            "by_group": by_group, "on_trial": by_question, "missed": missed}


def _rate(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def _ece(points: list[tuple[float, bool]], bins: int = 5) -> float | None:
    if not points:
        return None
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        group = [(p, ok) for p, ok in points if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if group:
            total += len(group) / len(points) * abs(sum(p for p, _ in group) / len(group) - sum(ok for _, ok in group) / len(group))
    return round(total, 4)


def question_measures(runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for qid in CHOICE_QS:
        asked = agreed = gold_n = correct = 0
        points: list[tuple[float, bool]] = []
        for run in runs:
            for entry in run["items"]:
                first, second = entry["raw"].get(qid), entry["raw"].get(qid + "_rev")
                if not first or not second:
                    continue
                asked += 1
                same = first["choice"] == second["choice"]
                agreed += same
                gold = entry["gold"].get("answers", {}).get(qid)
                if gold is None:
                    continue
                gold_n += 1
                if same:
                    ok = first["choice"] == gold
                    correct += ok
                    p = (first["probabilities"].get(first["choice"], 0) + second["probabilities"].get(first["choice"], 0)) / 2
                    points.append((float(p), ok))
        out[qid] = {"asked": asked, "both_order_agreement": _rate(agreed, asked), "gold_items": gold_n,
                    "agreement_with_gold": _rate(correct, gold_n), "ece": _ece(points)}
    for qid in NOUL_QS:
        pairs = []
        for run in runs:
            for entry in run["items"]:
                answer, gold = entry["raw"].get(qid), entry["gold"].get("answers", {}).get(qid)
                if answer and gold is not None:
                    pairs.append((float(answer["noul"]), bool(gold)))
        sweep = {}
        for t in SWEEP:
            tp = sum(1 for p, g in pairs if g and p >= t)
            fp = sum(1 for p, g in pairs if not g and p >= t)
            pos = sum(1 for _, g in pairs if g)
            sweep[str(t)] = {"recall": _rate(tp, pos), "false_positives": fp, "negatives": len(pairs) - pos}
        out[qid] = {"gold_items": len(pairs),
                    "brier": round(sum((p - g) ** 2 for p, g in pairs) / len(pairs), 4) if pairs else None,
                    "ece": _ece(pairs), "threshold_sweep": sweep}
    c4 = []
    for run in runs:
        expect = {tuple(sorted(c["lead_ids"])): c.get("c4") for c in run["planted"] if c.get("c4")}
        for conflict in run["conflicts"]:
            key = tuple(sorted(conflict["lead_ids"]))
            if key in expect:
                c4.append((conflict["answer"].get("counted"), conflict["answer"].get("value") == expect[key]))
    out["c4_conflict"] = {"gold_pairs": len(c4), "both_order_agreement": _rate(sum(1 for c, _ in c4 if c), len(c4)),
                          "agreement_with_gold": _rate(sum(1 for c, ok in c4 if c and ok), len(c4))}
    return out


def adds_over_rules(runs: list[Mapping[str, Any]]) -> dict[str, int]:
    """Per question: failures caught or planted clues found that no rule caught."""
    gain = {qid: 0 for qid in (*CHOICE_QS, *NOUL_QS, "c4_conflict")}
    for run in runs:
        for entry in run["items"]:
            failure = entry["gold"].get("failure") or {}
            if failure.get("research_visible") and failure.get("class"):
                found = signals(entry) & CATCH[failure["class"]]
                rule_found = {s for s in found if s in RULE_SIGNALS or s[5:] in entry["rule_clues"]}
                if found and not rule_found:
                    for qid in {s.split("=")[0] for s in found if "=" in s}:
                        gain[qid] += 1
        page = {e["item_id"]: (set(e["page_clues"]), set(e["rule_clues"])) for e in run["items"]}
        for clue in run["planted"]:
            on_page = any(clue["group"] in page[i][0] for i in clue["lead_ids"])
            by_rule = any(clue["group"] in page[i][1] for i in clue["lead_ids"])
            if on_page and not by_rule and clue["group"] in CLUE_QUESTION:
                gain[CLUE_QUESTION[clue["group"]]] += 1
    return gain


def cost_measures(provider: Any | None, leads: int) -> dict[str, Any]:
    calls = getattr(provider, "calls", [])
    costs = [c["cost"] for c in calls if c["cost"] is not None]
    latencies = sorted(c["latency_ms"] for c in calls)
    p95 = latencies[min(len(latencies) - 1, math.ceil(0.95 * len(latencies)) - 1)] if latencies else None
    total = round(sum(costs), 6) if costs else (0.0 if provider is None else None)
    return {"requests": len(calls), "failed_requests": getattr(provider, "failures", 0),
            "cost_usd": total, "cost_per_100_leads_usd": round(100 * total / leads, 6) if total is not None and leads else None,
            "latency_ms_mean": round(sum(latencies) / len(latencies)) if latencies else None, "latency_ms_p95": p95,
            "egress_bytes": sum(c["bytes_out"] for c in calls), "egress_hosts": [provider.host] if calls else []}


def measure(contender: str, runs: list[Mapping[str, Any]], provider: Any | None) -> dict[str, Any]:
    leads = sum(1 for run in runs for e in run["items"] if not e["absence"])
    fallbacks = sum(1 for run in runs for e in run["items"]
                    if e["result"] and contender != "rules" and e["result"].get("backend") != contender)
    real = [r for r in runs if not r["synthetic"]]
    return {
        "contender": contender, "leads": leads, "fallbacks_to_rules": fallbacks,
        "triage_violations": sum(len(r["violations"]) for r in runs),
        "invalid_leads": sum(len(r["invalid_leads"]) for r in runs),
        "lost_leads": lost_leads(runs),
        "lost_leads_by_fixture": {r["fixture"]: lost_leads([r]) for r in runs},
        "research_visible_failures": failures_caught(real),
        "clues": clue_measures(runs),
        "questions": question_measures(runs) if provider is not None else {},
        "adds_over_rules": adds_over_rules(runs) if provider is not None else {},
        "cost": cost_measures(provider, leads),
    }


# ------------------------------------------------------------------ targets
def check_targets(metrics: Mapping[str, Any], targets: Mapping[str, Any]) -> dict[str, Any]:
    """Per target: pass, fail or unmeasured. A contender passes only when every target is measured and met."""
    t = targets["targets"]
    q = metrics["questions"]
    checks: dict[str, tuple[Any, Any, str]] = {
        "lost_leads_max": (metrics["lost_leads"]["lost"], t["lost_leads_max"], "max"),
        "research_visible_caught_min": (metrics["research_visible_failures"]["rate"], t["research_visible_caught_min"], "min"),
        "clue_recall_min": (metrics["clues"]["recall"], t["clue_recall_min"], "min"),
        "false_clues_per_100_max": (metrics["clues"]["false_clues_per_100_leads"], t["false_clues_per_100_max"], "max"),
        "fallbacks_max": (metrics["fallbacks_to_rules"], t["fallbacks_max"], "max"),
        "triage_violations_max": (metrics["triage_violations"], 0, "max"),
        "cost_per_100_leads_usd_max": (metrics["cost"]["cost_per_100_leads_usd"], t["cost_per_100_leads_usd_max"], "max"),
        "latency_ms_p95_max": (metrics["cost"]["latency_ms_p95"] if metrics["cost"]["requests"] else 0, t["latency_ms_p95_max"], "max"),
    }
    for qid in CHOICE_QS:
        if qid in q:
            checks[f"{qid}_both_order_min"] = (q[qid]["both_order_agreement"], t["both_order_agreement_min"], "min")
            if qid != "q2_direction":
                checks[f"{qid}_gold_agreement_min"] = (q[qid]["agreement_with_gold"], t["question_agreement_min"], "min")
                checks[f"{qid}_ece_max"] = (q[qid]["ece"], t["ece_max"], "max")
    out = {}
    for name, (value, limit, kind) in checks.items():
        status = "unmeasured" if value is None else ("pass" if (value <= limit if kind == "max" else value >= limit) else "fail")
        out[name] = {"value": value, "target": limit, "status": status}
    statuses = {r["status"] for r in out.values()}
    return {"checks": out, "verdict": "pass" if statuses == {"pass"} else ("fail" if "fail" in statuses else "incomplete")}


def questions_to_cut(metrics: Mapping[str, Any], targets: Mapping[str, Any]) -> dict[str, Any]:
    """Questions the cut rule removes: below the agreement target, an on-trial
    question below its planted recall, or one that adds nothing over the
    rules. "Adds nothing" is decided only once there was something to add:
    labelled research-visible failures for Q1-Q6, planted clues for C1-C4."""
    t = targets["targets"]
    labelled = metrics["research_visible_failures"]["research_visible"] > 0
    planted_groups = {g for g, row in metrics["clues"]["by_group"].items() if row["planted"]}
    cut, undecided = [], []
    for qid, gain in metrics["adds_over_rules"].items():
        row = metrics["questions"].get(qid, {})
        low = row.get("agreement_with_gold") is not None and row["agreement_with_gold"] < t["question_agreement_min"]
        trial = metrics["clues"]["on_trial"].get(qid) if qid in NOUL_QS else None
        weak = bool(trial) and (_rate(trial["found"], trial["planted"]) or 0) < t["on_trial_planted_recall_min"]
        clue_q = qid in CLUE_QUESTION.values()
        decidable = any(CLUE_QUESTION.get(g) == qid for g in planted_groups) if clue_q else labelled
        if qid == "c4_conflict":
            decidable = False  # C4 only removes K2 conflicts; judged by its agreement, never by gain
        if low or weak or (decidable and gain == 0):
            cut.append(qid)
        elif not decidable:
            undecided.append(qid)
    return {"cut": sorted(cut), "undecided": sorted(undecided)}


def choose_backend(results: list[Mapping[str, Any]]) -> str | None:
    passing = [r for r in results if r["targets"]["verdict"] == "pass" and r["metrics"]["contender"] != "rules"]
    if not passing:
        return None
    best = min(passing, key=lambda r: (r["metrics"]["lost_leads"]["lost"],
                                      -(r["metrics"]["research_visible_failures"]["rate"] or 0),
                                      r["metrics"]["cost"]["cost_per_100_leads_usd"] or 0))
    return best["metrics"]["contender"]


# ------------------------------------------------------------------ CLI
def _outside_checkout(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise AcceptanceError(f"{path} is inside the public checkout; private detail stays outside it")
    return path


def run(args: argparse.Namespace, provider_factory: Callable[[str], tuple[Any | None, str]] | None = None) -> dict[str, Any]:
    targets_path = Path(args.targets)
    targets = load_targets(targets_path)
    if args.split == "heldout":
        require_heldout_allowed(targets)
    fixtures = [load_fixture(p) for p in fixture_paths(args.fixture or [])]
    acceptance = trial_acceptance(targets)
    results = []
    for contender in args.contender:
        provider, host = (provider_factory or (lambda c: build_provider(c, args)))(contender)
        metered = Metered(provider, host) if provider is not None else None
        runs = [run_case(f, args.split, contender, metered, acceptance) for f in fixtures]
        metrics = measure(contender, runs, metered)
        result = {"metrics": metrics, "targets": check_targets(metrics, targets),
                  "cut": questions_to_cut(metrics, targets) if metered else {"cut": [], "undecided": []}}
        if args.private_detail:
            folder = _outside_checkout(Path(args.private_detail))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"detail-{contender}-{args.split}.json").write_text(json.dumps(runs, indent=1, ensure_ascii=False) + "\n")
        result["synthetic_detail"] = [{k: e[k] for k in ("item_id", "page_clues", "rule_clues")}
                                      | {"disposition": (e["result"] or {}).get("disposition"),
                                         "flags": [f["reason"] for f in (e["result"] or {}).get("flags", [])]}
                                      for r in runs if r["synthetic"] for e in r["items"]]
        results.append(result)
    record = {
        "record_version": "1.0", "split": args.split, "bank_version": rq.RESEARCH_BANK_VERSION,
        "targets_sha256": sha256_file(targets_path), "targets_status": targets.get("status"),
        "trial_thresholds": targets["thresholds"],
        "fixtures": [{"name": f["name"], "synthetic": f["synthetic"], "items": sum(1 for i in f["items"] if i["split"] == args.split)}
                     for f in fixtures],
        "ran_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "results": results,
        "chosen_backend": choose_backend(results) if args.split == "heldout" else None,
    }
    return record


def acceptance_record(record: Mapping[str, Any], results_sha256: str) -> dict[str, Any]:
    """What the flag-only switch reads (research_rules.ACCEPTANCE), from a passing held-out run."""
    if record["split"] != "heldout" or record["targets_status"] != "approved" or not record["chosen_backend"]:
        raise AcceptanceError("an acceptance record needs a held-out run against approved targets with a passing contender")
    chosen = next(r for r in record["results"] if r["metrics"]["contender"] == record["chosen_backend"])
    return {"passed": True, "bank_version": record["bank_version"], "backend": record["chosen_backend"],
            "thresholds": record["trial_thresholds"], "cut": chosen["cut"]["cut"], "targets_sha256": record["targets_sha256"],
            "results_sha256": results_sha256, "decided_at": record["ran_at"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--split", choices=("dev", "heldout"), required=True)
    parser.add_argument("--contender", action="append", choices=CONTENDERS, required=True)
    parser.add_argument("--fixture", action="append", help="another fixture file (e.g. the private claims fixture)")
    parser.add_argument("--targets", default=str(TARGETS))
    parser.add_argument("--out", help="write the results record here")
    parser.add_argument("--private-detail", help="directory outside the checkout for per-item detail of every fixture")
    parser.add_argument("--openrouter-key-file", help="file holding the OpenRouter key (default: $OPENROUTER_API_KEY)")
    parser.add_argument("--jev-model", default=client.DEFAULT_MODEL)
    parser.add_argument("--local-url", help="llama-server on loopback, e.g. http://127.0.0.1:8080")
    parser.add_argument("--write-acceptance", action="store_true",
                        help="after a passing held-out run, write integrations/decisions/research-acceptance.json")
    args = parser.parse_args(argv)
    try:
        record = run(args)
        payload = (json.dumps(record, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
        if args.out:
            Path(args.out).write_bytes(payload)
        if args.write_acceptance:
            accepted = acceptance_record(record, hashlib.sha256(payload).hexdigest())
            rr.ACCEPTANCE_FILE.write_text(json.dumps(accepted, indent=1) + "\n", encoding="utf-8")
    except AcceptanceError as exc:
        print(f"acceptance: {exc}", file=sys.stderr)
        return 2
    summary = [{"contender": r["metrics"]["contender"], "verdict": r["targets"]["verdict"],
                "lost_leads": r["metrics"]["lost_leads"]["lost"], "clue_recall": r["metrics"]["clues"]["recall"],
                "false_clues_per_100": r["metrics"]["clues"]["false_clues_per_100_leads"],
                "visible_failures_caught": r["metrics"]["research_visible_failures"]["rate"],
                "cost_usd": r["metrics"]["cost"]["cost_usd"]} for r in record["results"]]
    print(json.dumps({"split": record["split"], "summary": summary, "chosen_backend": record["chosen_backend"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
