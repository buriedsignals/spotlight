"""The Research gate page model: clues first, then directions with their leads,
then the leads the page keeps apart (circumstantial, flagged, set aside,
unverifiable, snippet only), the research mode, the spend and what the round left out.

Layout below "clues first": each clue lists the leads it rests on; each
direction lists its remaining leads in rank order, with its gap and its
counts; leads triage found to be about another subject or event
(circumstantial: kept, never set aside for it), leads that triage flagged,
that were set aside, whose excerpt was not
found verbatim, or that are only a search snippet each get their own section,
so none of them is hidden inside a direction. Every lead appears once outside
the clues.

The member marks clues (pursue or dismiss; K6 watchlist and C1 premise clues
are always pursued) and directions (keep or drop; a new direction is added).
The investigator's suggested marks, when present, prefill the marks and the
review file keeps each suggestion beside the member's final mark.

``data/research-suggestions.json`` (written by the investigator's AMENDMENT
mode before the page opens) follows schemas/research-suggestions.schema.json.
The gate's rule (research.suggestions_problems) decides whether they count:
suggestions it refuses block the page and prefill nothing.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .contract import OrchestrationError, RESEARCH_OUTPUTS, SCRIPT_DIR
from .research import suggestions_problems
from .storage import case_path

TRIAGE = "data/research-triage.json"
SUGGESTIONS = "data/research-suggestions.json"
ROUNDS = "research/rounds.json"
# What the page shows besides the leads and clues the receipt binds; the
# page's digest covers them, so a decision binds what the member saw.
PAGE_INPUTS = (TRIAGE, SUGGESTIONS, ROUNDS)

MODE_LABELS = {
    "local_only": "Local lanes only",
    "openrouter_swarm": "OpenRouter swarm",
    "subscription_subagents": "Subscription subagents",
    "newsroom": "Newsroom",
}
CLUE_MARKS = ("pursue", "dismiss")
# Where a lead is listed below the clues (``_section``).
SECTIONS = ("direction", "circumstantial", "flagged", "set_aside", "unverifiable", "snippet")
# The triage context label (research_rules): a kept lead, never set aside for it.
CIRCUMSTANTIAL = "circumstantial"
CONTEXT_LABELS = (CIRCUMSTANTIAL,)
PLANNED_MARKS = ("keep", "drop")
NEW_MARK = "add"
# K6 watchlist and C1 premise clues are always shown and never dismissed (D7).
PROTECTED_TYPES = frozenset({"watchlist", "premise_complicated", "premise_contradicted"})
# Clues a lead's triage answers raise, by the question that raised them. The
# watchlist and conflict clues are already in data/clues.json (K6, K2).
TRIAGE_CLUE_SOURCES = {"premise_complicated": "C1", "premise_contradicted": "C1",
                       "new_actor": "C2", "change_or_absence": "C3"}
TRIAGE_CLUE_SUMMARIES = {
    "premise_complicated": "Triage: this item complicates the brief's premise.",
    "premise_contradicted": "Triage: this item contradicts the brief's premise.",
    "new_actor": "Triage: this item connects a brief subject to someone the brief does not name.",
    "change_or_absence": "Triage: this item shows a change, or a record that is missing.",
}
ABSENCE_WARNING = "Absence is not clearance."
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_LEADS = 500
MAX_CLUES = 200
MAX_LABEL = 1000

_merge_module: Any = None


def _merge() -> Any:
    """scripts/research-merge.py: the ranking (U6) and, through it, the
    triage rules (D7). Loaded once, only when a Research page is built."""
    global _merge_module
    if _merge_module is None:
        spec = importlib.util.spec_from_file_location("spotlight_research_merge", SCRIPT_DIR / "research-merge.py")
        if spec is None or spec.loader is None:
            raise OrchestrationError("scripts/research-merge.py is missing")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _merge_module = module
    return _merge_module


def _text(value: object, limit: int = 4000) -> str:
    if value is None:
        return ""
    return str(value)[:limit]


def _read(case: Path, relative: str) -> tuple[Any, bool]:
    """(value, present). A present but unreadable file reads as (None, True)."""
    path = case_path(case, relative)
    if not path.is_file() or path.is_symlink():
        return None, False
    try:
        return json.loads(path.read_text(encoding="utf-8")), True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, True


def shown_hashes(case: Path) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {}
    for relative in PAGE_INPUTS:
        path = case_path(case, relative)
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return hashes


# ---------------------------------------------------------------- run summary


def _run_summary(leads: Mapping[str, Any], rounds: Any, plan: Mapping[str, Any]) -> dict[str, Any]:
    ledger = rounds.get("rounds") if isinstance(rounds, dict) else None
    latest = ledger[-1] if isinstance(ledger, list) and ledger and isinstance(ledger[-1], dict) else {}
    mode = leads.get("mode") or latest.get("mode") or plan.get("mode") or ""
    mode = mode if mode in MODE_LABELS else ""
    left_out = {}
    for entry in latest.get("left_out") or []:
        if isinstance(entry, dict) and entry.get("topic_id"):
            left_out[_text(entry["topic_id"], 200)] = _text(entry.get("reason"), 500)
    for topic in leads.get("left_out") or []:
        left_out.setdefault(_text(topic, 200), "")
    refused = [{"topic_id": _text(e.get("topic_id"), 200), "reason": _text(e.get("reason"), 500)}
               for e in latest.get("refused") or [] if isinstance(e, dict)]
    stopped = latest.get("stopped") if isinstance(latest.get("stopped"), dict) else None
    spends = [r.get("spent_usd") for r in ledger or [] if isinstance(r, dict)]
    numbers = [s for s in spends if isinstance(s, (int, float))]
    spent = latest.get("spent_usd", leads.get("spend_usd"))
    return {
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, "Not recorded"),
        "round": latest.get("round", leads.get("round")),
        "spend": {
            # Subscription subagents report no spend.
            "reported": mode != "subscription_subagents" and isinstance(spent, (int, float)),
            "round_usd": spent if isinstance(spent, (int, float)) else None,
            "budget_usd": latest.get("budget_usd", plan.get("round_budget_usd")),
            "case_usd": sum(numbers) if numbers else None,
            "case_budget_usd": plan.get("case_budget_usd"),
        },
        "left_out": [{"topic_id": topic, "reason": reason} for topic, reason in sorted(left_out.items())],
        "refused": refused,
        "stopped": {"code": _text(stopped.get("code"), 80), "message": _text(stopped.get("message"), 1000)}
        if stopped else None,
        "lanes": [{"topic_id": _text(lane.get("topic_id"), 200), "status": _text(lane.get("status"), 40)}
                  for lane in leads.get("lanes") or []
                  if isinstance(lane, dict) and lane.get("status") != "completed"],
    }


# ---------------------------------------------------------------- triage


def _triage_summary(triage: Any, present: bool, rr: Any) -> tuple[dict[str, Any], list[str]]:
    flag_only = rr.triage_mode() != rr.FULL
    if not present:
        return {"state": "missing", "flag_only": flag_only, "notice": rr.FLAG_ONLY_NOTICE if flag_only else ""}, []
    if not isinstance(triage, dict):
        return {"state": "broken", "flag_only": flag_only, "notice": ""}, [f"{TRIAGE} is unreadable"]
    violations = rr.triage_violations(triage)
    mode = triage.get("mode")
    backends = sorted({_text(r.get("backend"), 40) for r in triage.get("leads") or [] if isinstance(r, dict) and r.get("backend")})
    summary = {
        "state": "broken" if violations else "ok",
        "flag_only": mode != rr.FULL,
        "notice": _text(triage.get("notice") or (rr.FLAG_ONLY_NOTICE if mode != rr.FULL else ""), 1000),
        "backends": backends,
        "bank_version": _text(triage.get("bank_version"), 80),
        "invalid_leads": len(triage.get("invalid_leads") or []),
    }
    return summary, [f"triage broke its rules (D7): {v}" for v in violations[:20]]


# ---------------------------------------------------------------- leads


def _section(lead: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    """Where a lead is listed below the clues. Set aside first (lost leads hide
    there), then snippet only (a snippet has no stored source, so it is also
    unverifiable), then unverifiable, then flagged, then circumstantial (about
    another subject or event); the rest under their direction."""
    if lead.get("status") == "set_aside" or result.get("disposition") == "set_aside":
        return "set_aside"
    if lead.get("stored") == "snippet":
        return "snippet"
    if lead.get("status") == "unverifiable":
        return "unverifiable"
    if result.get("disposition") == "flag":
        return "flagged"
    if CIRCUMSTANTIAL in (result.get("context") or []):
        return "circumstantial"
    return "direction"


def _lead_view(entry: Mapping[str, Any], rank: int, ranked: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    lead = entry["lead"]
    excerpt = lead.get("excerpt") if isinstance(lead.get("excerpt"), dict) else {}
    capture = lead.get("capture") if isinstance(lead.get("capture"), dict) else {}
    attribution = lead.get("attribution") if isinstance(lead.get("attribution"), dict) else None
    labels = result.get("labels") if isinstance(result.get("labels"), dict) else {}
    return {
        "lead_id": _text(lead.get("lead_id"), 200),
        "rank": rank,
        "section": _section(lead, result),
        "topic_id": _text(lead.get("topic_id"), 200),
        "direction_id": _text(lead.get("direction_id"), 200),
        "summary": _text(lead.get("summary")),
        "subjects": [_text(s, 300) for s in (lead.get("subjects") or [])[:20]],
        "absence": lead.get("absence") is True,
        "queries_tried": [_text(q, 500) for q in (lead.get("queries_tried") or [])[:20]],
        "status": _text(lead.get("status"), 40),
        "stored": _text(lead.get("stored"), 40),
        "excerpt": _text(excerpt.get("text")),
        "direct_quote": excerpt.get("direct_quote") is True,
        "source_url": _text(capture.get("source_url"), 2000),
        "attribution": {"actor": _text((attribution.get("actor") or {}).get("label"), 300),
                        "stance": _text(attribution.get("stance"), 40),
                        "verbatim": _text(attribution.get("attribution_verbatim"), 1000)} if attribution else None,
        "copies": len(entry.get("copies") or []),
        "journalist_input": lead.get("journalist_input") is True,
        "clues": list(ranked.get("clues") or []),
        "source_families": ranked.get("source_families", 0),
        "triage": {
            "disposition": _text(result.get("disposition"), 40),
            "flags": [{"reason": _text(f.get("reason"), 80), "detail": _text(f.get("detail"), 500)}
                      for f in result.get("flags") or [] if isinstance(f, dict)],
            "labels": {k: _text(v, 80) for k, v in labels.items() if isinstance(k, str)},
            "set_aside_reasons": [_text(r, 80) for r in result.get("set_aside_reasons") or []],
            "context": [c for c in result.get("context") or [] if c in CONTEXT_LABELS],
            "backend": _text(result.get("backend"), 40),
        } if result else None,
    }


# ---------------------------------------------------------------- clues


def _protected(clue: Mapping[str, Any]) -> bool:
    return clue.get("type") in PROTECTED_TYPES or clue.get("source") in ("K6", "C1") or clue.get("always_shown") is True


def _clue_views(clues_doc: Mapping[str, Any], triage: Any, positions: Mapping[str, int], merge: Any) -> list[dict[str, Any]]:
    clues: list[dict[str, Any]] = []
    conflicts = {tuple(sorted(c.get("lead_ids") or [])): c for c in (triage or {}).get("conflicts") or []
                 if isinstance(c, dict)} if isinstance(triage, dict) else {}
    for clue in (clues_doc.get("clues") or [])[:MAX_CLUES]:
        if not isinstance(clue, dict) or not isinstance(clue.get("clue_id"), str) or clue.get("type") not in merge.CLUE_RANK_GROUP:
            continue
        view = {
            "clue_id": _text(clue["clue_id"], 200), "type": clue["type"], "source": _text(clue.get("source"), 120),
            "summary": _text(clue.get("summary")), "warning": _text(clue.get("warning"), 500),
            "lead_ids": [_text(i, 200) for i in clue.get("lead_ids") or []][:50],
            "direction_ids": [_text(i, 200) for i in clue.get("direction_ids") or []][:20],
            "attribute": _text(clue.get("attribute"), 200), "model_unsure": clue.get("model_unsure") is True,
            "protected": _protected(clue), "conflict_answer": None,
        }
        answer = conflicts.get(tuple(sorted(view["lead_ids"])))
        if clue.get("source") == "K2" and answer:
            recorded = answer.get("answer") if isinstance(answer.get("answer"), dict) else {}
            view["conflict_answer"] = {"value": _text(recorded.get("value"), 80), "counted": recorded.get("counted") is True}
            if recorded and not recorded.get("counted"):
                view["model_unsure"] = True
        clues.append(view)
    known = {(lead_id, c["type"]) for c in clues for lead_id in c["lead_ids"]}
    for result in (triage or {}).get("leads") or [] if isinstance(triage, dict) else []:
        if not isinstance(result, dict) or not isinstance(result.get("lead_id"), str):
            continue
        for kind in result.get("clues") or []:
            source = TRIAGE_CLUE_SOURCES.get(kind)
            if source is None or (result["lead_id"], kind) in known:
                continue
            known.add((result["lead_id"], kind))
            clues.append({
                "clue_id": f"{source.lower()}-{_text(result['lead_id'], 200)}", "type": kind, "source": source,
                "summary": TRIAGE_CLUE_SUMMARIES[kind],
                "warning": ABSENCE_WARNING if kind == "change_or_absence" else "",
                "lead_ids": [_text(result["lead_id"], 200)], "direction_ids": [], "attribute": "",
                "model_unsure": False, "protected": kind in PROTECTED_TYPES, "conflict_answer": None,
            })
    worst = len(positions) + 1
    clues.sort(key=lambda c: (merge.CLUE_ORDER.index(merge.CLUE_RANK_GROUP[c["type"]]),
                              min((positions.get(i, worst) for i in c["lead_ids"]), default=worst),
                              c["source"], c["clue_id"]))
    return clues


# ---------------------------------------------------------------- suggestions and items


def _suggestions(document: Any) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    clues: dict[str, dict[str, str]] = {}
    directions: dict[str, dict[str, str]] = {}
    if not isinstance(document, dict):
        return clues, directions
    for entry in document.get("clues") or []:
        if isinstance(entry, dict) and isinstance(entry.get("clue_id"), str) and entry.get("mark") in CLUE_MARKS:
            clues[entry["clue_id"]] = {"mark": entry["mark"], "reason": _text(entry.get("reason"), 1000)}
    for entry in document.get("directions") or []:
        if (isinstance(entry, dict) and isinstance(entry.get("direction_id"), str)
                and entry.get("mark") in (*PLANNED_MARKS, NEW_MARK)):
            directions[entry["direction_id"]] = {"mark": entry["mark"], "reason": _text(entry.get("reason"), 1000),
                                                 "direction": _text(entry.get("direction"), MAX_LABEL)}
    return clues, directions


SUGGESTED_BY = "investigator suggestion"
ADDED_BY_MEMBER = "added by the member"


def _clue_item(clue: Mapping[str, Any], suggestion: Mapping[str, str] | None) -> dict[str, Any]:
    key = f"clue:{clue['clue_id']}"
    item = {"key": key, "kind": "clue", "target": key, "required": True, "protected": clue["protected"]}
    if clue["protected"]:
        # Always pursued: the only mark offered, whatever was suggested.
        reason = "K6 watchlist and C1 premise clues are always pursued."
        if suggestion and suggestion["mark"] != "pursue":
            reason += f" The investigator suggested {suggestion['mark']}: {suggestion['reason']}"
        elif suggestion:
            reason = suggestion["reason"] or reason
        return {**item, "suggested": "pursue", "suggestion_source": SUGGESTED_BY if suggestion else "rule",
                "suggestion_reason": reason}
    if suggestion:
        return {**item, "suggested": suggestion["mark"], "suggestion_source": SUGGESTED_BY,
                "suggestion_reason": suggestion["reason"]}
    return {**item, "suggested": None, "suggestion_source": "none", "suggestion_reason": "No suggestion for this clue."}


def _direction_items(planned: dict[str, str], suggested: Mapping[str, Mapping[str, str]],
                     saved: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = []
    for direction_id, label in planned.items():
        key = f"direction:{direction_id}"
        suggestion = suggested.get(direction_id)
        mark = suggestion["mark"] if suggestion and suggestion["mark"] in PLANNED_MARKS else None
        items.append({"key": key, "kind": "direction", "target": key, "label": label, "planned": True,
                      "required": False, "suggested": mark,
                      "suggestion_source": SUGGESTED_BY if mark else "none",
                      "suggestion_reason": suggestion["reason"] if mark else "No mark keeps the direction in the plan."})
    for direction_id, suggestion in suggested.items():
        if direction_id in planned or suggestion["mark"] != NEW_MARK or not SLUG.match(direction_id):
            continue
        key = f"direction:{direction_id}"
        items.append({"key": key, "kind": "direction", "target": key, "planned": False, "required": False,
                      "label": suggestion["direction"] or direction_id, "suggested": NEW_MARK,
                      "suggestion_source": SUGGESTED_BY, "suggestion_reason": suggestion["reason"]})
    shown = {item["key"] for item in items}
    for key, record in saved.items():
        if (isinstance(record, dict) and key.startswith("direction:") and key not in shown
                and record.get("added_by") == "member"):
            items.append({"key": key, "kind": "direction", "target": key, "planned": False, "required": False,
                          "label": _text(record.get("label"), MAX_LABEL), "added_by": "member", "suggested": None,
                          "suggestion_source": ADDED_BY_MEMBER, "suggestion_reason": ""})
    return items


def _planned_directions(methodology: Any) -> dict[str, str]:
    planned: dict[str, str] = {}
    for direction in (methodology or {}).get("investigation_plan") or [] if isinstance(methodology, dict) else []:
        if isinstance(direction, dict):
            # The same identity research_checks.d8_amendment uses.
            identity = direction.get("direction_id") or direction.get("direction")
            if isinstance(identity, str) and identity:
                planned[identity] = _text(direction.get("direction") or identity, MAX_LABEL)
    return planned


# ---------------------------------------------------------------- page model


def content(case: Path, saved_items: Mapping[str, Any]) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """(content, blocking failures, review items) for the Research gate."""
    leads_doc, _ = _read(case, RESEARCH_OUTPUTS[0])
    clues_doc, _ = _read(case, RESEARCH_OUTPUTS[1])
    blocking = [f"{relative} is missing or unreadable"
                for relative, value in zip(RESEARCH_OUTPUTS, (leads_doc, clues_doc)) if not isinstance(value, dict)]
    leads_doc = leads_doc if isinstance(leads_doc, dict) else {}
    clues_doc = clues_doc if isinstance(clues_doc, dict) else {}
    methodology, _ = _read(case, "data/methodology.json")
    plan = methodology.get("research_plan") if isinstance(methodology, dict) and isinstance(methodology.get("research_plan"), dict) else {}
    triage, triage_present = _read(case, TRIAGE)
    suggestions, suggestions_present = _read(case, SUGGESTIONS)
    rounds, _ = _read(case, ROUNDS)
    merge = _merge()

    triage_view, triage_blocking = _triage_summary(triage, triage_present, merge.rr)
    blocking += triage_blocking
    suggestions_state = "present" if isinstance(suggestions, dict) else "missing"
    if suggestions_present and not isinstance(suggestions, dict):
        blocking.append(f"{SUGGESTIONS} is unreadable")
    elif suggestions_present:
        # The gate's own rule (schema, inputs bound to these leads, clues and
        # triage, every clue and planned direction marked) decides whether
        # the suggestions count; refused ones do not prefill the marks.
        refused = suggestions_problems(case)
        if refused:
            blocking += refused
            suggestions, suggestions_state = None, "refused"
    triage_doc = triage if isinstance(triage, dict) and triage_view["state"] == "ok" else None

    entries = [e for e in leads_doc.get("leads") or [] if isinstance(e, dict) and isinstance(e.get("lead"), dict)
               and isinstance(e["lead"].get("lead_id"), str)]
    try:
        ranked = merge.rank({"leads": entries}, clues_doc, triage_doc) if entries else []
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        blocking.append(f"the leads could not be ranked: {exc}")
        ranked = [{"lead_id": e["lead"]["lead_id"]} for e in entries]
    by_id = {e["lead"]["lead_id"]: e for e in entries}
    results = {r["lead_id"]: r for r in (triage_doc or {}).get("leads") or [] if isinstance(r, dict) and "lead_id" in r}
    leads = [_lead_view(by_id[r["lead_id"]], n, r, results.get(r["lead_id"], {}))
             for n, r in enumerate(ranked[:MAX_LEADS], 1)]
    positions = {lead["lead_id"]: lead["rank"] for lead in leads}

    clues = _clue_views(clues_doc, triage_doc, positions, merge)
    clue_suggestions, direction_suggestions = _suggestions(suggestions)
    planned = _planned_directions(methodology)
    items = [_clue_item(clue, clue_suggestions.get(clue["clue_id"])) for clue in clues]
    items += _direction_items(planned, direction_suggestions, saved_items)

    gaps = {_text(g.get("direction_id"), 200): [_text(q, 500) for q in g.get("queries_tried") or []][:20]
            for g in leads_doc.get("gaps") or [] if isinstance(g, dict)}
    direction_ids = list(planned)
    for lead in leads:
        if lead["direction_id"] not in direction_ids:
            direction_ids.append(lead["direction_id"])
    directions = []
    for direction_id in direction_ids:
        own = [lead for lead in leads if lead["direction_id"] == direction_id]
        counts = {section: sum(1 for lead in own if lead["section"] == section)
                  for section in SECTIONS}
        directions.append({"direction_id": direction_id, "label": planned.get(direction_id, direction_id),
                           "planned": direction_id in planned, "gap": gaps.get(direction_id),
                           "counts": counts, "total": len(own)})

    return {
        "run": _run_summary(leads_doc, rounds, plan),
        "triage": triage_view,
        "suggestions": suggestions_state,
        "clues": clues,
        "directions": directions,
        "leads": leads,
        "lead_total": len(entries),
    }, blocking, items


def validate_mark(item: Mapping[str, Any], value: object) -> None:
    if item["kind"] == "clue":
        if value not in CLUE_MARKS:
            raise OrchestrationError("a clue mark is pursue or dismiss")
        if item.get("protected") and value != "pursue":
            raise OrchestrationError("K6 watchlist and C1 premise clues are always pursued")
        return
    if item.get("planned"):
        if value not in PLANNED_MARKS and value is not None:
            raise OrchestrationError("a planned direction is kept or dropped")
    elif value not in (NEW_MARK, None):
        raise OrchestrationError("a new direction is added, or withdrawn")


def added_direction(edit: Mapping[str, Any], items: Mapping[str, Any]) -> dict[str, Any]:
    """The review item for a direction the member adds on the page."""
    slug, label = edit.get("key"), edit.get("value")
    if not isinstance(slug, str) or not SLUG.match(slug) or len(slug) > 64:
        raise OrchestrationError("a new direction needs a short id of lowercase letters, digits and hyphens")
    if not isinstance(label, str) or not label.strip() or len(label) > MAX_LABEL:
        raise OrchestrationError(f"a new direction needs a description of at most {MAX_LABEL} characters")
    key = f"direction:{slug}"
    if key in items:
        raise OrchestrationError("that direction is already on the page")
    return {"key": key, "kind": "direction", "target": key, "planned": False, "required": False,
            "label": label.strip(), "added_by": "member", "suggested": None,
            "suggestion_source": ADDED_BY_MEMBER, "suggestion_reason": "", "final": NEW_MARK, "note": ""}
