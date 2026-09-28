"""Frozen question bank for Spotlight decision checks (Jev via OpenRouter).

Every question id maps to a Jev primitive. Choice questions listed in a
*_PERMUTE list are also asked with reversed option order (suffix "_rev"); a
choice only counts when both orders agree. Evaluation that produced these
questions and thresholds: tools/decision-model-evidence/jev-2026-09-28/.

Changing any question text changes QUESTION_BANK_VERSION; thresholds in
rules.py were measured against this exact text and must be re-measured.
"""

import hashlib
import json

YN = {"true": "Yes.", "false": "No."}


def noul(text):
    return {"type": "noul", "instructions": text, "criteria": YN}


def choice(text, criteria):
    return {"type": "choice", "instructions": text, "criteria": criteria}


def rev(d):
    return dict(reversed(list(d.items())))


# ---------------------------------------------------------------- grounding (R1)
SUPPORT = {
    "direct": "Read together with the source URL and heading, the sources state every element of the claim. The organisation or person may be identified by the page or document the text comes from. Translation, paraphrase and number formatting differences do not matter.",
    "partial": "At least one element the claim asserts is not stated: for example a date, a full name, an amount, that something is current rather than announced or planned, that a list is complete, or a legal status or domicile where the source only gives an address.",
    "insufficient": "The sources are on the same topic but do not state what the claim asserts.",
    "contradicted": "The sources state something incompatible with the claim, such as a different value, direction, role, date, entity, status, sequence or legal form.",
}
LEGAL = {
    "legal_fact": "The claim says where an organisation is legally domiciled, seated, registered or incorporated, and the sources state that as a legal seat, domicile, registered office, registration or place of incorporation.",
    "address_only": "The claim says where an organisation is legally domiciled, seated, registered or incorporated, but the sources give only an address, city or headquarters location.",
    "not_applicable": "The claim does not say where an organisation is legally domiciled, seated, registered or incorporated.",
}
ATTRIBUTION = {
    "asserted": "The sources state the content the claim relies on in their own voice, as an official record, filing or register entry, or as a finding or decision of a court or authority.",
    "reported_claim": "The sources only report that someone says, alleges, claims, suspects, charges or accuses it, for example a prosecutor's indictment, a complaint, a spokesperson or an anonymous source.",
    "not_found": "The sources do not contain the content the claim relies on.",
}
DIRECTION = {
    "same_direction": "The sources give the same payer and recipient, lender and borrower, owner and owned, or awarding and winning party as the claim.",
    "reversed_or_different": "The sources give a reversed or different payer, recipient, lender, borrower, owner, owned entity or party.",
    "not_stated": "The sources do not say who the parties are.",
    "not_applicable": "The claim does not say who paid, lent, transferred, owns, controls or awarded something to whom.",
}
CONTROL = {
    "beneficial_owner": "The sources name the beneficial, ultimate or economic owner (for example wirtschaftlich Berechtigter, bénéficiaire effectif, titolare effettivo, titular real).",
    "shareholding": "The sources give a shareholding, ownership stake, sole shareholder, voting control or parent-subsidiary relationship, but not who ultimately benefits.",
    "role_only": "The sources give only a role such as director, board member, manager, managing officer, signatory, liquidator, auditor or employee.",
    "indirect_only": "The sources give only an indirect link such as a shared address, family tie, business associate or common director.",
    "not_applicable": "The claim does not say that anyone owns or controls an organisation or asset.",
}
STAGE = {
    "proposed_or_requested": "Proposed, requested, planned, budgeted, applied for, announced or under negotiation.",
    "approved_or_decided": "Approved, decided, awarded, ordered, signed or agreed, but not stated as paid or carried out.",
    "paid_or_executed": "Paid, transferred, disbursed, carried out or completed.",
    "not_applicable": "No payment, fine, subsidy, budget, loan, contract, award or capital change is involved.",
}
MATCH4 = lambda what: {  # noqa: E731
    "all_match": f"Every {what} in the claim is stated in the sources with the same value. Formatting differences such as 4,2 Mio., 4.2 million or 4'200'000 count as the same value.",
    "mismatch": f"At least one {what} in the claim differs from the value in the sources.",
    "not_stated": f"At least one {what} in the claim does not appear in the sources.",
    "not_applicable": f"The claim contains no {what}.",
}
AMOUNTS = MATCH4("amount, percentage, share count or currency")
DATES = MATCH4("date, year or period")

GROUNDING = {
    "g_support": choice("Compare the claim against the sources. Which describes how well the sources ground the claim?", SUPPORT),
    "g_current": noul("Do the sources present the claimed situation as having happened or as current, rather than planned, announced for the future, or proposed?"),
    "g_complete": noul("If the claim presents a list as complete (for example 'consists of' or 'the board is'), do the sources give that complete list? Answer yes if the claim presents no such list."),
    "g_legal": choice("How do the sources support any statement in the claim about where an organisation is legally based?", LEGAL),
    "g_attribution": choice("How do the sources present the content that the claim relies on?", ATTRIBUTION),
    "g_claim_attributed": noul("Does the claim itself attribute its content to someone (for example 'according to', 'X alleges', 'reportedly', 'is accused of', 'selon', 'laut', 'secondo', 'según') rather than stating it as fact?"),
    "g_direction": choice("If the claim says who paid, lent, transferred, owns, controls or awarded something to whom, how do the sources compare?", DIRECTION),
    "g_control": choice("If the claim says that someone owns or controls an organisation or asset, what do the sources establish about that link?", CONTROL),
    "g_claim_beneficial": noul("Does the claim say that someone is the beneficial, ultimate, real or economic owner of something?"),
    "g_stage_source": choice("If money or a decision is involved (payment, fine, subsidy, budget, loan, contract, award, capital change), what stage do the SOURCES establish?", STAGE),
    "g_stage_claim": choice("If money or a decision is involved (payment, fine, subsidy, budget, loan, contract, award, capital change), what stage does the CLAIM assert?", STAGE),
    "g_amounts": choice("How do the amounts, percentages, share counts and currencies in the claim compare with the sources?", AMOUNTS),
    "g_dates": choice("How do the dates, years and periods in the claim compare with the sources?", DATES),
    "g_scope": noul("Does the claim generalise beyond the sources, for example saying 'all', 'always', 'every', 'entire' or 'systematically' where the sources describe only some cases, one instance or a part?"),
    "g_causation": noul("Does the claim state a cause, purpose or motive (for example 'because', 'in order to', 'to avoid', 'in exchange for') that the sources do not state?"),
    "g_arithmetic": noul("Does checking the claim require adding, subtracting, counting, averaging or converting numbers, rather than reading a figure that the sources state directly?"),
}
GROUNDING_PERMUTE = ["g_support", "g_legal", "g_direction", "g_amounts", "g_dates"]

# ------------------------------------------------------- granular categories
CLAIM_TYPE = {
    "ownership_control": "Who owns, controls or holds shares in an organisation or asset.",
    "payment_transfer": "A payment, transfer, loan, donation or other movement of money.",
    "contract_procurement": "A contract, tender, public procurement award or commercial agreement.",
    "appointment_role": "Who holds or held a position, role or function.",
    "legal_status_registration": "Legal form, registration, seat, domicile, identifier or corporate status.",
    "legal_proceeding": "An investigation, charge, lawsuit, court decision, sanction or fine.",
    "financial_metric": "Revenue, profit, budget, capital, valuation or another financial figure.",
    "statement": "What someone said, wrote or announced.",
    "event": "Something that happened at a time and place.",
    "relationship": "A personal, family or business relationship between people or organisations.",
    "other": "None of the above.",
}
TEMPORAL = {
    "current": "The claim says something is true now.",
    "historical": "The claim says something was true or happened in the past.",
    "announced_or_planned": "The claim says something is planned, proposed, announced or expected.",
    "timeless": "The claim has no time dimension, such as an identifier or a definition.",
}
CATEGORIES = {
    "c_claim_type": choice("What kind of claim is this? Judge the claim only.", CLAIM_TYPE),
    "c_temporal": choice("How does the claim present the timing of what it asserts? Judge the claim only.", TEMPORAL),
}

# ------------------------------------------------------------- report (R2)
FIDELITY = {
    "faithful": "The sentence says no more than the approved finding: same entities, actors, scope, time, amounts and certainty. It keeps any qualification the verdict and confidence require: an unverified, disputed or false finding is not presented as established, and a partially verified finding is hedged. Plain-language paraphrase, translation and explaining why a verified fact matters are faithful.",
    "overstated": "The sentence adds certainty, causation, motive, scope, dates, amounts, entities or actors that the finding does not contain, drops an attribution such as 'allegedly', or presents as established something the verdict does not establish.",
    "unrelated": "The sentence is about something other than the finding.",
}
OVERSTATEMENT = {
    "certainty": "Presents something as more certain than the finding.",
    "causation": "Adds a cause or effect.",
    "motive": "Adds an intention or motive.",
    "scope": "Widens the scope, number or extent.",
    "time": "Adds or changes a date or duration.",
    "entity_or_actor": "Adds or changes a person, organisation or actor.",
    "amount": "Adds or changes an amount.",
    "dropped_qualifier": "Drops a qualification such as 'about', 'at least', 'proposed' or 'partly'.",
    "verdict_ignored": "States as established what the verdict does not establish.",
    "allegation_as_fact": "Drops an attribution such as 'allegedly' or 'according to'.",
    "none": "The sentence does not overstate the finding.",
}
REPORT = {
    "r_fidelity": choice("Compare the sentence against the approved finding, its verdict and its confidence. Which describes the sentence?", FIDELITY),
    "r_type": choice("If the sentence overstates the finding, what is the main way it does so?", OVERSTATEMENT),
    "r_debunk": noul("Does the sentence only say that the finding's claim is false, did not happen or is not true, without adding any other fact, cause or motive?"),
    "r_scope": noul("Does the sentence describe a larger group, number, share or extent than the finding does (for example 'the employees' or 'all employees' where the finding says two employees)?"),
    "r_actor": noul("Does the sentence name a different person, office or body as the one who acted than the finding does (for example 'the chief executive' where the finding says 'the finance department')?"),
}
REPORT_PERMUTE = ["r_fidelity"]

# ------------------------------------------------------------- contradictions
CONTRADICTION = {
    "value_mismatch": "The two sources give incompatible values for the same attribute, scope and time.",
    "time_impossible": "The events the sources assert cannot coexist in the stated chronology.",
    "existence_dispute": "One source asserts that something exists or happened; the other asserts that it does not or did not.",
    "connection_paradox": "The two sources assert relationships that appear mutually exclusive.",
    "location_impossible": "The subject cannot be in both places at the stated times.",
    "not_a_contradiction": "The sources only differ in scope, time window, role, units, rounding or naming, and can both be true.",
}
CONTRA = {"k_category": choice("Compare passage_a and passage_b with respect to the claim. Which describes how they relate?", CONTRADICTION)}
CONTRA_PERMUTE = ["k_category"]

# ------------------------------------------------------------- ingest
PROPOSITION = {
    "equivalent": "The proposition means the same as the finding claim, including a faithful translation or paraphrase.",
    "narrower": "The proposition says strictly less than the finding claim, for example by dropping a date or secondary detail, and asserts nothing beyond it.",
    "broader_or_changed": "The proposition asserts more than the finding claim, or changes its certainty, scope, status, attribution, amount, date, direction, entity or polarity.",
    "different": "The proposition is a different statement altogether.",
}
MEMBERSHIP = {
    "supports": "The claim is evidence that the event happened as described.",
    "contradicts": "The claim is incompatible with the event as described.",
    "contextualizes": "The claim gives relevant background such as causes, prior relationships or setting, without establishing the event.",
    "mentions": "The claim only refers to an actor or object of the event without bearing on whether it happened.",
}
ENTITY = {
    "person": "A human being.",
    "company": "A commercial business entity, such as an AG, SA, GmbH, Sàrl, Ltd, Inc, S.p.A., S.L., holding company or fund manager.",
    "organization": "A non-commercial organisation, such as a government body, ministry, court, police force, NGO, foundation, association, political party, international body or university.",
    "place": "A geographic or administrative location or an address, such as a country, canton, city, region, river or street address.",
    "unclear": "The sentence does not allow a decision.",
}
MATCHING = {
    "same_claim": "Both state the same proposition, possibly in different words or languages.",
    "updates": "Same subject and attribute, but the new claim describes a later state or newer value; both can be true at their own times.",
    "contradicts": "Both cannot be true for the same time and scope.",
    "related": "Same entities or topic, but a different proposition.",
    "unrelated": "No meaningful connection.",
}
INGEST = {
    "proposition": {"i_prop": choice("Compare the proposition with the finding claim it was derived from. Which describes the proposition?", PROPOSITION)},
    "membership": {"i_member": choice("How does the claim relate to the event?", MEMBERSHIP)},
    "entities": {"i_entity": choice("What kind of entity is entity_name, as used in context_sentence?", ENTITY),
                 "i_entity_ambiguous": noul("Could entity_name, as used in context_sentence, plausibly be more than one kind of entity, for example a person, a company or a place with the same name, because the sentence gives no title, legal form, role or other clue?")},
    "matching": {"i_match": choice("Compare new_claim with existing_claim from the knowledge base. Which describes their relationship?", MATCHING)},
}
INGEST_PERMUTE = {"proposition": ["i_prop"], "membership": ["i_member"], "entities": [], "matching": ["i_match"]}


def with_permutations(questions, permute):
    out = dict(questions)
    for q in permute:
        out[q + "_rev"] = {**questions[q], "criteria": rev(questions[q]["criteria"])}
    return out


ALL = {
    "grounding": with_permutations({**GROUNDING, **CATEGORIES}, GROUNDING_PERMUTE),
    "report": with_permutations(REPORT, REPORT_PERMUTE),
    "contradiction": with_permutations(CONTRA, CONTRA_PERMUTE),
    "proposition": with_permutations(INGEST["proposition"], INGEST_PERMUTE["proposition"]),
    "membership": with_permutations(INGEST["membership"], INGEST_PERMUTE["membership"]),
    "entities": with_permutations(INGEST["entities"], INGEST_PERMUTE["entities"]),
    "matching": with_permutations(INGEST["matching"], INGEST_PERMUTE["matching"]),
}
QUESTION_BANK_VERSION = hashlib.sha256(
    json.dumps(ALL, sort_keys=True, ensure_ascii=False).encode("utf-8")
).hexdigest()
