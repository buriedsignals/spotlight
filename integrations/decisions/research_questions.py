"""Question bank for research triage (the Research phase's decision questions).

Kept apart from ``questions.ALL``: these questions read research leads, not
findings, and carry their own version. Changing any text here changes
``RESEARCH_BANK_VERSION``; thresholds measured against one version never
apply to another. So does a change to what an answer does
(``EFFECTS_REVISION``, applied in ``research_rules.lead_outcome``).

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
    "same_identifier": "Yes: it shares a registry number, LEI, tax id, date of birth or exact address with one named in the premise.",
    "consistent_details": "Yes: the name matches and the other details fit, but no identifier is shared.",
    "name_only": "Only the name matches; nothing else shows it is the same one.",
    "different_subject_or_event": "No: it is about a different person, company or event that shares a name or topic with one in the premise.",
    "none_of_the_brief": "No: it is about none of the people, companies or events in the premise.",
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
    "asserted": "As a fact, record or decision, in its own voice.",
    "reported_claim": "As something someone claims, alleges or reports.",
    "interpretation": "As its own analysis, estimate or opinion.",
}
Q5_STATUS = {
    "done": "Yes: the excerpt says it happened or was carried out.",
    "decided_not_done": "It was approved, awarded or signed, but the excerpt does not say it was carried out.",
    "proposed_or_offered": "No: it was only planned, announced, advertised, recruited for or applied for.",
    "not_established": "The excerpt does not say whether it happened.",
    "no_event": "The summary describes a fact or a state, not an action or event.",
}
Q6_FIDELITY = {
    "faithful": "Yes: the excerpt says everything the summary says. Translation and paraphrase are fine.",
    "widens": "No: the summary says more than the excerpt: a bigger amount or group, a firmer status, a stronger link, or another actor or figure.",
    "contradicts": "No: the excerpt says the opposite of the summary or gives a different value.",
    "unsupported": "No: the summary says something the excerpt does not mention at all.",
}
C1_PREMISE = {
    "supports": "Backs it up, or adds detail that fits it.",
    "complicates": "Adds a condition, another explanation, a missing piece or another actor.",
    "contradicts": "Says something that cannot be true if the premise is true, or someone denies it.",
    "unrelated": "Says nothing about the premise.",
}
C4_CONFLICT = {
    "agree": "Both items give the same value.",
    "differ_but_compatible": "The values differ, but both can be true, for example at different times or for different scopes.",
    "conflict": "Both values cannot be true for the same subject, time and scope.",
    "not_comparable": "The items do not give comparable values for this attribute.",
}
DIRECTION_NONE = "The item informs none of the approved research directions."

QUESTIONS = {
    "q1_identity": choice("Is the excerpt about a person, company or event named in the premise?", Q1_IDENTITY),
    "q3_source": choice("Who is speaking in this item's source?", Q3_SOURCE),
    "q4_assertion": choice("How does the excerpt state what the summary says?", Q4_ASSERTION),
    "q5_status": choice("Does the excerpt show that the action or event in the summary actually happened?", Q5_STATUS),
    "q6_fidelity": choice("Compare the summary with the excerpt. Does the excerpt back up the summary?", Q6_FIDELITY),
    "c1_premise": choice("Read the premise, then the excerpt. What does the excerpt do to the premise?", C1_PREMISE),
}
ON_TRIAL = {
    "c2_new_actor": noul("Does the item connect a brief subject to a person or organisation the brief does not name, as owner, director, shareholder, payer, recipient or relative, or through a shared address, phone or email?"),
    "c3_change": noul("Does the item show a subject changing its name, owner, address, legal form or registration, or a record being edited, backdated, removed, withdrawn or not found?"),
}
DIRECTION_TEXT = "Which approved research direction does this item inform?"
CONFLICT_TEXT = "Compare item A and item B about the same subject. How do their values for {attribute} compare?"

# What the answers do (research_rules.lead_outcome). Raise it whenever an
# effect changes, so an acceptance measured under the old effects stops
# counting. 2: an answer that only narrows to the brief's subjects or
# directions never sets a lead aside; it labels it circumstantial or outside
# the directions. 3 (bank v3): Q4 has no not_found answer (D3 checks the
# excerpt against the stored source), so no model answer sets a lead aside.
EFFECTS_REVISION = 3

# Every choice question is asked in both option orders.
PERMUTED = ("q1_identity", "q2_direction", "q3_source", "q4_assertion", "q5_status", "q6_fidelity", "c1_premise", "c4_conflict")

RESEARCH_BANK_VERSION = hashlib.sha256(json.dumps(
    {"questions": QUESTIONS, "on_trial": ON_TRIAL, "direction": [DIRECTION_TEXT, DIRECTION_NONE],
     "conflict": [CONFLICT_TEXT, C4_CONFLICT], "permuted": PERMUTED, "effects": EFFECTS_REVISION},
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
