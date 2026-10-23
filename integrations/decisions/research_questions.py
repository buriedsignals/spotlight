"""Question bank for research triage (the Research phase's decision questions).

Kept apart from ``questions.ALL``: these questions read research leads, not
findings, and carry their own version. Changing any text here changes
``RESEARCH_BANK_VERSION``; thresholds measured against one version never
apply to another.

Q1-Q6 and C1, C4 are ``choice`` questions, asked in both option orders (the
``_rev`` twin). C2 and C3 are ``noul`` questions on trial: they are asked only
once the acceptance test has passed for this bank version (see
``research_rules.triage_mode``).

Q2's options are the case's approved research directions, so they change per
case; ``direction_question`` builds it. C4 is asked only on rule K2's pairs
and names the compared attribute; ``conflict_question`` builds it. The bank
version hashes the templates, not the per-case options.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping

from questions import choice, noul, rev

Q1_IDENTITY = {
    "same_identifier": "The item shares a registry number, LEI, tax id, date of birth or exact address with a subject or event in the brief.",
    "consistent_details": "The name and other details match a subject or event in the brief, but no identifier is shared.",
    "name_only": "Only the name matches a subject or event in the brief; nothing else in the item confirms it is the same one.",
    "different_subject_or_event": "The item is about a different subject or event that shares a name or topic with one in the brief.",
    "none_of_the_brief": "The item is about none of the subjects or events in the brief.",
}
Q3_SOURCE = {
    "primary_record": "A register, filing, court record, procurement notice or dataset entry.",
    "original_reporting": "Reporting that gathered the information itself, such as a news investigation with its own interviews or documents.",
    "secondary": "A summary, aggregator, repost, or a text citing another source for the information.",
    "interested_party": "The subject, a government, party, company, campaign or state media with a stake in the matter.",
    "user_generated": "A social media post, forum message, review or other content posted by an individual user.",
    "unclear": "The item does not show who is speaking.",
}
Q4_ASSERTION = {
    "asserted": "The source states the content in its own voice, as a fact, record or decision.",
    "reported_claim": "The source reports that someone alleges, claims or reports it.",
    "interpretation": "The content is the source's own inference, analysis or opinion.",
    "not_found": "The source does not contain the content of the item.",
}
Q5_STATUS = {
    "done": "The item establishes that it happened or was carried out.",
    "decided_not_done": "Approved, awarded or signed, but not shown to be carried out.",
    "proposed_or_offered": "Planned, announced, advertised, recruited for or applied for.",
    "not_established": "The item does not show whether it happened.",
    "no_event": "The item describes no event or action.",
}
Q6_FIDELITY = {
    "faithful": "The summary says no more than the excerpt: same actors, scope, status, relationships and figures. Translation and paraphrase are faithful.",
    "widens": "The summary describes a larger group or scope, a firmer status, a stronger relationship, or a different actor or figure than the excerpt.",
    "contradicts": "The summary states something the excerpt contradicts.",
    "unsupported": "The excerpt does not contain what the summary says.",
}
C1_PREMISE = {
    "supports": "The item is consistent with the premise and adds to it.",
    "complicates": "The item adds a condition, an alternative explanation, a missing piece or a different actor to the premise.",
    "contradicts": "The item states something incompatible with the premise, including a denial by an actor.",
    "unrelated": "The item does not bear on the premise.",
}
C4_CONFLICT = {
    "agree": "Both items give the same value.",
    "differ_but_compatible": "The values differ, but both can be true, for example at different times or for different scopes.",
    "conflict": "Both values cannot be true for the same subject, time and scope.",
    "not_comparable": "The items do not give comparable values for this attribute.",
}
DIRECTION_NONE = "The item informs none of the approved research directions."

QUESTIONS = {
    "q1_identity": choice("Compare the item with the subjects and events in the brief. Which describes what the item is about?", Q1_IDENTITY),
    "q3_source": choice("Who is speaking in this item's source?", Q3_SOURCE),
    "q4_assertion": choice("How does the source present the content of this item?", Q4_ASSERTION),
    "q5_status": choice("What does the item establish actually happened?", Q5_STATUS),
    "q6_fidelity": choice("Compare the lane's summary with its verbatim excerpt. Which describes the summary?", Q6_FIDELITY),
    "c1_premise": choice("Read the brief's premise and the item. How does the item relate to the premise?", C1_PREMISE),
}
ON_TRIAL = {
    "c2_new_actor": noul("Does the item connect a brief subject to a person or organisation the brief does not name, as owner, director, shareholder, payer, recipient or relative, or through a shared address, phone or email?"),
    "c3_change": noul("Does the item show a subject changing its name, owner, address, legal form or registration, or a record being edited, backdated, removed, withdrawn or not found?"),
}
DIRECTION_TEXT = "Which approved research direction does this item inform?"
CONFLICT_TEXT = "Compare item A and item B about the same subject. How do their values for {attribute} compare?"

# Every choice question is asked in both option orders.
PERMUTED = ("q1_identity", "q2_direction", "q3_source", "q4_assertion", "q5_status", "q6_fidelity", "c1_premise", "c4_conflict")

RESEARCH_BANK_VERSION = hashlib.sha256(json.dumps(
    {"questions": QUESTIONS, "on_trial": ON_TRIAL, "direction": [DIRECTION_TEXT, DIRECTION_NONE],
     "conflict": [CONFLICT_TEXT, C4_CONFLICT], "permuted": PERMUTED},
    sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

DIRECTION_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def direction_question(directions: Mapping[str, str]) -> dict:
    """Q2 with one option per approved direction (id -> label) plus ``none``."""
    if not directions or any(not DIRECTION_ID.match(d) or d == "none" for d in directions):
        raise ValueError("Q2 needs approved direction ids (lowercase slugs, not 'none')")
    criteria = {d: f"The item informs this direction: {label}" for d, label in directions.items()}
    criteria["none"] = DIRECTION_NONE
    return choice(DIRECTION_TEXT, criteria)


def conflict_question(attribute: str) -> dict:
    attribute = " ".join(str(attribute).split())[:120]
    if not attribute:
        raise ValueError("C4 needs the compared attribute")
    return choice(CONFLICT_TEXT.format(attribute=attribute), C4_CONFLICT)


def with_reversed(questions: Mapping[str, dict]) -> dict[str, dict]:
    """The questions plus a ``_rev`` twin, options reversed, for every choice."""
    out = dict(questions)
    for qid, question in questions.items():
        if question["type"] == "choice":
            out[qid + "_rev"] = {**question, "criteria": rev(question["criteria"])}
    return out
