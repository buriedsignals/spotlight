"""Deterministic rules for research triage: the lead seam, S1/S2/N1, the
effects of the research questions, and the triage invariants (D7).

A lead is never set aside for being about another subject, event or
direction than the brief's: leads about other people, companies or events can
be circumstantial, and that is what makes them clues. Such answers label the
lead (``CIRCUMSTANTIAL``, ``OUTSIDE_DIRECTIONS``). No answer sets a lead
aside: whether the source holds what the lead says is D3's job (the excerpt
must be verbatim in a stored source, or the lead is marked unverifiable), not
a question's.

Nothing here calls a model. A model answer has an effect only when both
option orders agree; disagreement flags the lead "model unsure" and changes
nothing else. Until the decision-model acceptance test has passed for this
bank version (``ACCEPTANCE``), triage only flags: the on-trial questions C2
and C3 are not asked and no probability band is assigned.

Triage reads only the lead fields ``parse_lead`` accepts and type-checks; any
other field of a lead never reaches a question.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from research_questions import ON_TRIAL, RESEARCH_BANK_VERSION

# Written by the decision-model acceptance test (evals/decision-acceptance)
# once a held-out run passes: the chosen backend, the bank version, the frozen
# thresholds (C2, C3) and any questions it cut. Absent until then, which keeps
# triage in flag-only mode; a record for another bank version changes nothing.
ACCEPTANCE_FILE = Path(__file__).resolve().parent / "research-acceptance.json"


def load_acceptance(path: Path = ACCEPTANCE_FILE) -> Mapping[str, Any] | None:
    """The acceptance record, or None when there is none or it is unreadable."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


ACCEPTANCE: Mapping[str, Any] | None = load_acceptance()

FLAG_ONLY, FULL = "flag_only", "full"
FLAG_ONLY_NOTICE = ("Triage only flags until the decision-model acceptance test passes: the new-actor and "
                    "change-or-absence questions are not asked.")
DISPOSITIONS = ("keep", "flag", "set_aside")
ALWAYS_SHOWN_CLUES = {"watchlist", "premise_complicated", "premise_contradicted"}
# Reasons triage may set a lead aside: none. D3 checks that the source holds
# the excerpt; topical answers (another subject, no direction) only label.
SET_ASIDE_REASONS: frozenset[str] = frozenset()
# Context labels: kept leads, ranked after same-subject leads.
CIRCUMSTANTIAL, OUTSIDE_DIRECTIONS = "circumstantial", "outside_directions"
CIRCUMSTANTIAL_IDENTITIES = frozenset({"different_subject_or_event", "none_of_the_brief"})
LEAD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_TEXT = 4000


class LeadInvalid(ValueError):
    """A lead lacks a field triage needs, or a field has the wrong type."""


@dataclass(frozen=True)
class TriageLead:
    """The lead fields triage reads. Everything else in a lead is ignored."""

    lead_id: str
    topic_id: str
    direction_id: str
    summary: str
    subjects: tuple[str, ...]
    excerpt: str
    language: str
    stored: str  # "page" or "snippet"
    source_url: str
    content_sha256: str
    journalist_input: bool


@dataclass(frozen=True)
class TriageBrief:
    premise: str
    subjects: tuple[str, ...]
    directions: Mapping[str, str]  # approved direction id -> label


def _text(raw: Mapping[str, Any], key: str, *, required: bool = True, limit: int = MAX_TEXT) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise LeadInvalid(f"{key} must be a non-empty string" if required else f"{key} must be a string")
    if len(value) > limit:
        raise LeadInvalid(f"{key} is longer than {limit} characters")
    return value


def parse_lead(raw: Any) -> TriageLead:
    """Type-check the fields triage reads from a research lead
    (schemas/research-lead.schema.json, already validated by the caller;
    absence leads excluded) and drop the rest."""
    if not isinstance(raw, Mapping):
        raise LeadInvalid("a lead must be an object")
    lead_id = _text(raw, "lead_id", limit=128)
    if not LEAD_ID.match(lead_id):
        raise LeadInvalid("lead_id has unexpected characters")
    topic_id, direction_id = _text(raw, "topic_id", limit=64), _text(raw, "direction_id", limit=64)
    if not SLUG.match(topic_id) or not SLUG.match(direction_id):
        raise LeadInvalid("topic_id and direction_id must be lowercase slugs")
    subjects = raw.get("subjects", [])
    if not isinstance(subjects, list) or not all(isinstance(s, str) and s.strip() for s in subjects):
        raise LeadInvalid("subjects must be a list of strings")
    excerpt = raw.get("excerpt") or {}
    if not isinstance(excerpt, Mapping):
        raise LeadInvalid("excerpt must be an object")
    stored = raw.get("stored")
    if stored not in ("page", "snippet"):
        raise LeadInvalid("stored must be 'page' or 'snippet'")
    capture = raw.get("capture") or {}
    if not isinstance(capture, Mapping):
        raise LeadInvalid("capture must be an object")
    journalist_input = raw.get("journalist_input", False)
    if not isinstance(journalist_input, bool):
        raise LeadInvalid("journalist_input must be true or false")
    return TriageLead(
        lead_id=lead_id, topic_id=topic_id, direction_id=direction_id,
        summary=_text(raw, "summary"), subjects=tuple(subjects[:20]),
        excerpt=_text(excerpt, "text"), language=_text(excerpt, "language", required=False, limit=16),
        stored=stored, source_url=_text(capture, "source_url", required=False, limit=2048),
        content_sha256=_text(raw, "content_sha256", required=False, limit=64),
        journalist_input=journalist_input,
    )


def lead_state(lead: TriageLead) -> dict[str, Any]:
    """What a question sees of one lead: delimited data, never instructions."""
    return {"item": {"summary": lead.summary, "excerpt": lead.excerpt, "language": lead.language,
                     "subjects": list(lead.subjects), "source_url": lead.source_url, "stored": lead.stored}}


# ------------------------------------------------------------- mode switch
def triage_mode(acceptance: Mapping[str, Any] | None = ACCEPTANCE) -> str:
    """FULL only after the acceptance test passed for this exact bank version."""
    if (isinstance(acceptance, Mapping) and acceptance.get("passed") is True
            and acceptance.get("bank_version") == RESEARCH_BANK_VERSION):
        return FULL
    return FLAG_ONLY


def on_trial_asked(mode: str, acceptance: Mapping[str, Any] | None = ACCEPTANCE) -> list[str]:
    if mode != FULL or not isinstance(acceptance, Mapping):
        return []
    cut = set(acceptance.get("cut") or ())
    return [qid for qid in ON_TRIAL if qid not in cut]


# ------------------------------------------------------------- S1, S2, N1
def snippet_only(lead: TriageLead) -> bool:
    """S1: the stored source is a search snippet, not the page."""
    return lead.stored == "snippet"


_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu", "gv", "or", "ne"}


def source_family(lead: TriageLead) -> str:
    """The family a lead's source belongs to: its registrable domain (a
    two-label approximation, three labels under a country's second level such
    as co.uk). Material without a URL is its own family, by content."""
    host = (urlparse(lead.source_url).hostname or "").lower().removeprefix("www.")
    if not host:
        return "local:" + (lead.content_sha256 or lead.lead_id)
    labels = host.split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def source_families(leads: Iterable[TriageLead]) -> int:
    """S2: independent source families behind a lead group. A copy with the
    same content hash counts once, whatever site carried it."""
    seen_content: set[str] = set()
    families: set[str] = set()
    for lead in leads:
        if lead.content_sha256 and lead.content_sha256 in seen_content:
            continue
        if lead.content_sha256:
            seen_content.add(lead.content_sha256)
        families.add(source_family(lead))
    return len(families)


_FIGURE = re.compile(r"\d+(?:[.,'’   ]\d+)*")


def _figures(text: str) -> set[str]:
    return {re.sub(r"\D", "", match) for match in _FIGURE.findall(text)}


def missing_figures(lead: TriageLead) -> list[str]:
    """N1: figures in the summary that do not appear in the excerpt. Digits are
    compared without separators, so 4,2 and 4.2 match; 4.2 million against
    4,200,000 does not, and the summary is refused (rewrite it from the excerpt)."""
    return sorted(_figures(lead.summary) - _figures(lead.excerpt))


# ------------------------------------------------------------- answers
def agreed_choice(answers: Mapping[str, Any], qid: str) -> str | None:
    """The option both orders chose, or None when they disagree or either is missing."""
    first, second = answers.get(qid), answers.get(qid + "_rev")
    if not isinstance(first, Mapping) or not isinstance(second, Mapping):
        return None
    choice = first.get("choice")
    return choice if isinstance(choice, str) and choice == second.get("choice") else None


def record_answers(answers: Mapping[str, Any], choice_ids: Iterable[str], noul_ids: Iterable[str],
                   mode: str, acceptance: Mapping[str, Any] | None = ACCEPTANCE) -> dict[str, dict[str, Any]]:
    """Per question: what each order said, whether it counts, and its band
    (None until the acceptance test has frozen thresholds)."""
    thresholds = (acceptance or {}).get("thresholds") or {} if mode == FULL else {}
    out: dict[str, dict[str, Any]] = {}
    for qid in choice_ids:
        first, second = answers.get(qid) or {}, answers.get(qid + "_rev") or {}
        value = agreed_choice(answers, qid)
        out[qid] = {"choice": first.get("choice"), "choice_rev": second.get("choice"),
                    "counted": value is not None, "value": value, "band": None}
    for qid in noul_ids:
        p = (answers.get(qid) or {}).get("noul")
        threshold = thresholds.get(qid)
        band = None if threshold is None or not isinstance(p, (int, float)) else ("high" if p >= threshold else "low")
        out[qid] = {"noul": p, "counted": band is not None, "value": band, "band": band}
    return out


def lead_outcome(lead: TriageLead, recorded: Mapping[str, Mapping[str, Any]],
                 rule_clues: Iterable[str] = ()) -> dict[str, Any]:
    """Apply Appendix B's effects to one lead. Only counted answers act."""
    value = {qid: r["value"] for qid, r in recorded.items() if r.get("counted")}
    flags: list[dict[str, str]] = []
    labels: dict[str, str] = {}
    clues: list[str] = []
    context: list[str] = []
    rule_clues = set(rule_clues)

    def flag(reason: str, detail: str = "") -> None:
        flags.append({"reason": reason, **({"detail": detail} if detail else {})})

    unsure = sorted(qid for qid, r in recorded.items() if "choice" in r and not r["counted"]
                    and r.get("choice") is not None and r.get("choice_rev") is not None)
    if unsure:
        flag("model_unsure", ", ".join(unsure))

    identity = value.get("q1_identity")
    if identity:
        labels["identity"] = identity
    if identity == "name_only":
        flag("identity_unconfirmed")
    elif identity in CIRCUMSTANTIAL_IDENTITIES:
        context.append(CIRCUMSTANTIAL)
    if value.get("q2_direction") == "none":
        context.append(OUTSIDE_DIRECTIONS)
    if value.get("q3_source"):
        labels["source"] = value["q3_source"]
    assertion = value.get("q4_assertion")
    if assertion:
        labels["assertion"] = assertion
    if assertion == "reported_claim":
        flag("allegation")
    elif assertion == "interpretation":
        flag("interpretation", "never a factual lead")
    status = value.get("q5_status")
    if status:
        labels["status"] = status
    if status == "not_established":
        flag("status_unknown")
    fidelity = value.get("q6_fidelity")
    if fidelity and fidelity != "faithful":
        flag(f"summary_{fidelity}", "rewrite the summary from the excerpt")
    premise = value.get("c1_premise")
    if premise in ("complicates", "contradicts"):
        clues.append("premise_complicated" if premise == "complicates" else "premise_contradicted")
    if value.get("c2_new_actor") == "high":
        clues.append("new_actor")
    if value.get("c3_change") == "high":
        clues.append("change_or_absence")
        flag("absence_is_not_clearance")
    if "K6" in rule_clues:
        clues.append("watchlist")

    if snippet_only(lead):
        flag("snippet_only", "ranks below stored pages; cannot support a finding")
    missing = missing_figures(lead)
    if missing:
        flag("summary_refused", "figures not in the excerpt: " + ", ".join(missing))

    disposition = "flag" if flags or clues else "keep"
    position = None
    if premise == "contradicts" and assertion == "reported_claim":
        position = {"stance": "disputes"}  # an actor's denial of the premise
    return {"lead_id": lead.lead_id, "disposition": disposition, "set_aside_reasons": [],
            "flags": flags, "labels": labels, "clues": clues, "context": context, "summary_refused": bool(missing),
            "source_position": position}


def conflict_outcome(recorded: Mapping[str, Any]) -> str | None:
    """C4 on a K2 pair: 'conflict' becomes a clue when both orders agree."""
    return "conflict" if recorded.get("counted") and recorded.get("value") == "conflict" else None


# ------------------------------------------------------------- D7
def triage_violations(document: Mapping[str, Any]) -> list[str]:
    """D7: the model can only keep, flag or set aside; always-shown clues are
    never set aside; nothing is set aside before the acceptance test, nor for
    any reason outside ``SET_ASIDE_REASONS`` (none: a model answer never sets a
    lead aside); a counted answer rests on both orders agreeing; no band before U15."""
    problems: list[str] = []
    mode = document.get("mode")
    for result in document.get("leads", []):
        lid = result.get("lead_id", "?")
        if result.get("disposition") not in DISPOSITIONS:
            problems.append(f"{lid}: disposition {result.get('disposition')!r} is not keep, flag or set_aside")
        aside = result.get("disposition") == "set_aside"
        if aside and mode != FULL:
            problems.append(f"{lid}: set aside while triage only flags")
        if aside and ALWAYS_SHOWN_CLUES & set(result.get("clues", [])):
            problems.append(f"{lid}: an always-shown clue was set aside")
        if aside and not result.get("set_aside_reasons"):
            problems.append(f"{lid}: set aside without a reason")
        topical = sorted(set(result.get("set_aside_reasons") or []) - SET_ASIDE_REASONS) if aside else []
        if topical:
            problems.append(f"{lid}: set aside for {', '.join(map(str, topical))}, which never sets a lead aside")
        for qid, answer in (result.get("answers") or {}).items():
            if answer.get("band") is not None and mode != FULL:
                problems.append(f"{lid}: {qid} has a probability band before the acceptance test")
            if "choice" in answer and answer.get("counted") and answer.get("choice") != answer.get("choice_rev"):
                problems.append(f"{lid}: {qid} counted although the two orders disagree")
    return problems
