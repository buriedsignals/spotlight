#!/usr/bin/env python3
"""Merge the approved research lanes into data/leads.json and run the clue rules into data/clues.json.

    scripts/spotlight-python scripts/research-merge.py CASE_DIR [--round N]
    scripts/spotlight-python scripts/research-merge.py --rank CASE_DIR

Reads only the lane folders the approved research_plan names
(research/swarm/<topic-id>/: lane.json, leads.jsonl, sources/) and refuses
when any other folder exists there or a lane has not ended. Per lane:

- D2: lane.json and every lead line validate; an invalid lane is marked failed
  and none of its leads are merged.
- D3: a lead whose excerpt is not verbatim in its stored source becomes
  ``unverifiable``; it is kept, never deleted.
- D5: a lane that spent past its budget without stopping is reported partial.

Then D4 (merge by canonical URL and content hash: syndicated copies collapse,
the same source twice is not corroboration), D6 (each direction's gap, with
the queries tried) and the fixed clue rules K1-K6, with no model.
The output is deterministic: the same lanes give byte-identical files, so a
re-run never reopens the Research gate.

--rank prints the lead order for the Research page from data/leads.json,
data/clues.json and, when present, data/research-triage.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import itertools
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for _path in (SCRIPT_DIR, ROOT / "integrations" / "decisions"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import research_checks as checks  # noqa: E402
import research_rules as rr  # noqa: E402
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from spotlight_orchestration.case_writer import atomic_write_files  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.storage import transaction  # noqa: E402

SCHEMA_VERSION = "1.0"
ABSENCE_WARNING = "Absence is not clearance: a lookup that found nothing does not show that nothing exists."
# Lane types whose lookups are structured (a register, a court docket, an
# archive index): an empty answer there is an expected absence (K5).
STRUCTURED_LANES = {"registries", "news-courts", "archives", "technical"}
# Fact attributes that identify an entity, by kind (K4 compares within a kind).
IDENTIFIER_KINDS = {"address": "address", "registered_address": "address", "phone": "phone", "email": "email",
                    "director": "director", "agent": "agent", "registered_agent": "agent"}
WATCHLIST_TERMS = re.compile(
    r"\b(sanction(?:s|ed|ne[ds]?)?|sanktion(?:en|iert|sliste)?|sancion(?:es|ado|ada)?|politically exposed|"
    r"politiquement expos[ée]e?s?|politisch exponiert\w*|expuest[oa]s? pol[ií]ticamente|debar(?:red|ment)|"
    r"watch ?list(?:ed)?|black ?list(?:ed)?|asset freeze|frozen assets|gel des avoirs|verm[öo]genssperre)\b",
    re.IGNORECASE)
WATCHLIST_ACRONYMS = re.compile(r"\b(PEPs?|OFAC|SDN)\b")
WATCHLIST_HOSTS = ("opensanctions.org", "ofac.treas.gov", "sanctionssearch.ofac.treas.gov", "sam.gov", "sanctionsmap.eu")
TRACKING_PARAMS = re.compile(r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid|ref|ref_src)$", re.IGNORECASE)
MAX_PAIRS = 20  # per subject and attribute (K2) or subject (K3): the page stays readable

CLUE_ORDER = ("watchlist", "premise", "conflict", "new_actor", "change_or_absence", "timing", "shared_identifier",
              "method_signal")
CLUE_RANK_GROUP = {"watchlist": "watchlist", "premise_complicated": "premise", "premise_contradicted": "premise",
                   "conflict": "conflict", "new_actor": "new_actor", "change": "change_or_absence",
                   "absence": "change_or_absence", "change_or_absence": "change_or_absence",
                   "timing": "timing", "shared_identifier": "shared_identifier",
                   # Clues from Anomaly methods, which ran at Research before they moved to Investigation.
                   "method_signal": "method_signal"}
IDENTITY_ORDER = ("same_identifier", "consistent_details", "name_only")
SOURCE_ORDER = ("primary_record", "original_reporting", "secondary", "interested_party", "user_generated")
ASSERTION_ORDER = ("asserted", "reported_claim", "interpretation")


class MergeRefused(Exception):
    """The lanes cannot be merged as they are; nothing is written."""


# ------------------------------------------------------------------ helpers
def name_key(text: str) -> str:
    """Comparable form of a name or value: NFKC, casefold, punctuation to spaces."""
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.sub(r"[^\w@.+]+", " ", text).split())


def attribute_key(text: str) -> str:
    return re.sub(r"[\s-]+", "_", text.strip().casefold())


def canonical_url(url: str | None) -> str | None:
    """Scheme and host lowercased, www., default port, fragment and tracking
    parameters dropped, remaining query parameters sorted, no trailing slash."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    if not host:
        return None
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)))
    path = parts.path.rstrip("/") or ""
    return urlunsplit((parts.scheme.lower() or "https", netloc, path, query, ""))


def clue_id(source: str, key: object) -> str:
    return f"{source.lower()}-{hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]}"


def brief_text(methodology: Mapping[str, Any]) -> str:
    """What the brief names: the lead, the approved brief directions, the
    investigation directions and questions, and the topic briefs."""
    parts: list[str] = [str(methodology.get("lead") or "")]
    parts += [str(x) for x in methodology.get("brief_directions") or []]
    for direction in methodology.get("investigation_plan") or []:
        if isinstance(direction, Mapping):
            parts.append(str(direction.get("direction") or ""))
            parts += [str(q) for q in direction.get("questions") or []]
    for topic in checks.topics(checks.research_plan(methodology) or {}):
        parts += [str(q) for q in (topic.get("questions") or []) + (topic.get("seed_queries") or [])]
    return f" {name_key(' '.join(parts))} "


def named_in_brief(subject: str, brief: str) -> bool:
    key = name_key(subject)
    return bool(key) and f" {key} " in brief


def _family(lead: Mapping[str, Any]) -> str:
    return rr.source_family(rr.parse_lead(lead))


def _facts(lead: Mapping[str, Any]) -> list[tuple[str, str, str, str]]:
    """(subject key, attribute key, value key, value) for each fact whose value
    is verbatim in the lead's excerpt; any other fact is ignored."""
    excerpt = checks.normalized(str((lead.get("excerpt") or {}).get("text") or ""))
    out = []
    for fact in lead.get("facts") or []:
        value = str(fact.get("value") or "")
        if value and checks.normalized(value) in excerpt:
            out.append((name_key(str(fact.get("subject"))), attribute_key(str(fact.get("attribute"))),
                        name_key(value), value))
    return out


# ------------------------------------------------------------------ D2-D6 and the merge
def read_lanes(case: Path, plan: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Every approved lane: its merged-lanes entry, its leads (D3 applied) and the report lines."""
    unapproved = checks.d2_unapproved_folders(case, plan)
    if unapproved:
        raise MergeRefused("folders not in the approved research plan: " + ", ".join(unapproved))
    lane_entries: list[dict[str, Any]] = []
    leads: list[dict[str, Any]] = []
    report: list[str] = []
    for topic in sorted(checks.topics(plan), key=lambda t: str(t.get("topic_id"))):
        topic_id = str(topic.get("topic_id"))
        lane, lane_leads, raw, problems = checks.read_lane(case, topic)
        if lane is None and any("lane.json is missing" in p for p in problems):
            raise MergeRefused(f"{topic_id} has not ended (no lane.json)")
        if lane is not None and lane.get("status") == "running":
            raise MergeRefused(f"{topic_id} is still running")
        status = (lane or {}).get("status", "failed")
        if problems:
            report += [f"D2 {p}" for p in problems]
            status, lane_leads = "failed", []
        elif lane is not None:
            over = checks.d5_budget(topic, lane)
            report += [f"D5 {p}" for p in over]
            if over:
                status = "partial"
        folder = checks.lane_dir(case, topic_id)
        for lead in lane_leads:
            reason = checks.d3_excerpt(folder, lead)
            if reason and lead.get("status") == "candidate":
                lead = {**lead, "status": "unverifiable"}
                report.append(f"D3 {lead['lead_id']}: {reason}")
            leads.append({"lead": lead, "lane": lane or {}, "topic": topic})
        lane_entries.append({"topic_id": topic_id, "status": status,
                             "lane_sha256": checks.sha256_bytes(raw) if raw and not problems else None,
                             "_lane": lane or {}})
    return lane_entries, leads, report


def _keep_order(item: Mapping[str, Any]) -> tuple:
    lead = item["lead"]
    return (lead.get("stored") != "page", lead.get("status") != "candidate",
            (lead.get("capture") or {}).get("captured_at", ""), lead["topic_id"], lead["lead_id"])


def merge(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """D4: one entry per source. Leads sharing a canonical URL, an original's
    sha256 or an extracted text's sha256 collapse into one; the kept copy is a
    stored page before a snippet, verified before unverifiable, then earliest."""
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[tuple[str, str], int] = {}
    for index, item in enumerate(items):
        lead = item["lead"]
        if lead.get("absence"):
            continue
        keys = [("url", canonical_url((lead.get("capture") or {}).get("source_url")))]
        keys.append(("sha", lead.get("content_sha256")))
        keys += [("sha", d.get("content_sha256")) for d in lead.get("derivatives") or [] if d.get("type") == "extracted_text"]
        for key in keys:
            if key[1] is None:
                continue
            if key in owner:
                parent[find(index)] = find(owner[key])
            else:
                owner[key] = index
    groups: dict[int, list[dict[str, Any]]] = {}
    for index, item in enumerate(items):
        groups.setdefault(find(index), []).append(item)
    merged = []
    for members in groups.values():
        members.sort(key=_keep_order)
        kept = members[0]["lead"]
        merged.append({
            "merge_key": {"canonical_url": canonical_url((kept.get("capture") or {}).get("source_url")),
                          "content_sha256": kept.get("content_sha256")},
            "lead": kept,
            "copies": [{"topic_id": m["lead"]["topic_id"], "lead_id": m["lead"]["lead_id"]} for m in members[1:]],
        })
    merged.sort(key=lambda entry: (entry["lead"]["topic_id"], entry["lead"]["lead_id"]))
    ids = [entry["lead"]["lead_id"] for entry in merged]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise MergeRefused("two lanes gave different leads the same lead_id: " + ", ".join(duplicates))
    return merged


def build_leads(case: Path, methodology: Mapping[str, Any], round_number: int | None) -> tuple[dict[str, Any], list[str]]:
    plan = checks.research_plan(methodology)
    if plan is None:
        raise MergeRefused("the approved methodology has no research_plan")
    lane_entries, items, report = read_lanes(case, plan)
    merged = merge(items)
    report += [f"D4 {entry['lead']['lead_id']} absorbed {c['topic_id']}/{c['lead_id']}"
               for entry in merged for c in entry["copies"]]
    lanes_by_topic = {entry["topic_id"]: entry.pop("_lane") for entry in lane_entries}
    spend = round(sum(float((lane.get("spend") or {}).get("usd") or 0) for lane in lanes_by_topic.values()), 6)
    if spend > float(plan.get("case_budget_usd") or 0):
        report.append(f"D5 the round spent ${spend}, above the case budget")
    gaps = checks.d6_coverage((str(t.get("direction_id")) for t in checks.topics(plan)),
                              (entry["lead"] for entry in merged), lanes_by_topic, plan)
    report += [f"D6 {gap['direction_id']}: no qualifying lead" for gap in gaps]
    stamps = [str(lane.get(k)) for lane in lanes_by_topic.values() for k in ("finished_at", "started_at") if lane.get(k)]
    # Lanes with no time of their own keep the stamp of the merge already
    # written, so a rerun on the same inputs writes the same bytes.
    try:
        previous = read_json(case / "data" / "leads.json", False)
    except MergeRefused:
        previous = None  # an unreadable earlier merge is replaced, not reused
    kept = previous.get("generated_at") if isinstance(previous, dict) else None
    generated_at = (max(stamps) if stamps else kept if isinstance(kept, str) and kept
                    else dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"))
    document: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "generated_at": generated_at}
    if round_number is not None:
        document["round"] = round_number
    if plan.get("mode"):
        document["mode"] = plan["mode"]
    document.update({
        "spend_usd": spend,
        "lanes": lane_entries,
        "leads": merged,
        "left_out": sorted(e["topic_id"] for e in lane_entries if e["status"] == "not_started"),
        "gaps": gaps,
    })
    return document, report


# ------------------------------------------------------------------ K1-K6
def _clue(source: str, kind: str, lead_ids: Iterable[str], directions: Iterable[str], summary: str, key: object,
          **extra: Any) -> dict[str, Any]:
    return {"clue_id": clue_id(source, key), "type": kind, "source": source, "lead_ids": sorted(set(lead_ids)),
            "direction_ids": sorted(set(directions)), "summary": summary, **extra}


def clue_rules(leads_doc: Mapping[str, Any], methodology: Mapping[str, Any]) -> list[dict[str, Any]]:
    plan = checks.research_plan(methodology) or {}
    lane_type = {str(t.get("topic_id")): t.get("lane_type") for t in checks.topics(plan)}
    brief = brief_text(methodology)
    leads = [entry["lead"] for entry in leads_doc.get("leads") or []]
    sourced = [lead for lead in leads if not lead.get("absence")]
    verified = [lead for lead in sourced if lead.get("status") == "candidate"]
    clues: list[dict[str, Any]] = []

    # K1 convergence: a name the brief does not give, in two lanes from two source families.
    seen: dict[str, list[dict[str, Any]]] = {}
    for lead in verified:
        for subject in lead.get("subjects") or []:
            if not named_in_brief(subject, brief):
                seen.setdefault(name_key(subject), []).append(lead)
    for key, group in sorted(seen.items()):
        lanes = {lead["topic_id"] for lead in group}
        families = {_family(lead) for lead in group}
        if len(lanes) >= 2 and len(families) >= 2:
            label = next(s for s in group[0]["subjects"] if name_key(s) == key)
            clues.append(_clue("K1", "new_actor", (l["lead_id"] for l in group), (l["direction_id"] for l in group),
                               f"{label} is not named in the brief and appears in {len(lanes)} lanes from "
                               f"{len(families)} source families.", ["K1", key]))

    # K2 conflict pairs and K3 timing, from facts verbatim in the excerpts.
    values: dict[tuple[str, str], list[tuple[dict[str, Any], str, str]]] = {}
    events: dict[str, list[tuple[dict[str, Any], str, dt.date]]] = {}
    identifiers: dict[tuple[str, str], list[tuple[dict[str, Any], str]]] = {}
    for lead in verified:
        for subject, attribute, value_key, value in _facts(lead):
            values.setdefault((subject, attribute), []).append((lead, value_key, value))
            date = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", value)
            if date:
                try:
                    events.setdefault(subject, []).append((lead, attribute, dt.date(*map(int, date.groups()))))
                except ValueError:
                    pass
            if attribute in IDENTIFIER_KINDS:
                identifiers.setdefault((IDENTIFIER_KINDS[attribute], value_key), []).append((lead, subject))
    for (subject, attribute), group in sorted(values.items()):
        pairs = [(a, b) for a, b in itertools.combinations(sorted(group, key=lambda g: g[0]["lead_id"]), 2)
                 if a[1] != b[1] and a[0]["lead_id"] != b[0]["lead_id"]]
        for a, b in pairs[:MAX_PAIRS]:
            ids = [a[0]["lead_id"], b[0]["lead_id"]]
            clues.append(_clue("K2", "conflict", ids, (a[0]["direction_id"], b[0]["direction_id"]),
                               f"Two leads give different values for {attribute} of {subject}: "
                               f"\"{a[2]}\" and \"{b[2]}\". Sent to the conflict question.",
                               ["K2", subject, attribute, sorted(ids)], attribute=attribute))
    window = plan.get("timing_window_days")
    if isinstance(window, int) and window > 0:
        for subject, group in sorted(events.items()):
            distinct = sorted({(e[1], e[2], e[0]["lead_id"]): e for e in group}.values(),
                              key=lambda e: (e[2], e[1], e[0]["lead_id"]))
            pairs = [(a, b) for a, b in itertools.combinations(distinct, 2)
                     if (a[1], a[2]) != (b[1], b[2]) and abs((b[2] - a[2]).days) <= window]
            for a, b in pairs[:MAX_PAIRS]:
                clues.append(_clue("K3", "timing", (a[0]["lead_id"], b[0]["lead_id"]),
                                   (a[0]["direction_id"], b[0]["direction_id"]),
                                   f"{subject}: {a[1]} on {a[2].isoformat()} and {b[1]} on {b[2].isoformat()}, "
                                   f"within {window} days.", ["K3", subject, a[1], str(a[2]), b[1], str(b[2])]))

    # K4 shared identifier: one address, phone, email, director or agent for two or more entities.
    for (attribute, value_key), group in sorted(identifiers.items()):
        entities = sorted({subject for _lead, subject in group})
        if len(entities) >= 2:
            clues.append(_clue("K4", "shared_identifier", (l["lead_id"] for l, _s in group),
                               (l["direction_id"] for l, _s in group),
                               f"The same {attribute} is given for {len(entities)} entities: {', '.join(entities)}.",
                               ["K4", attribute, value_key]))

    # K5 expected absence: a structured lookup for a brief subject returned nothing.
    for lead in leads:
        if lead.get("absence") and lane_type.get(lead["topic_id"]) in STRUCTURED_LANES:
            subjects = [s for s in lead.get("subjects") or [] if named_in_brief(s, brief)]
            if subjects:
                clues.append(_clue("K5", "absence", [lead["lead_id"]], [lead["direction_id"]],
                                   f"No record found for {', '.join(subjects)} in a {lane_type[lead['topic_id']]} lookup.",
                                   ["K5", lead["lead_id"]], warning=ABSENCE_WARNING))

    # K6 watchlist: always shown, never set aside; read on every sourced lead.
    for lead in sourced:
        text = f"{lead.get('summary', '')} {(lead.get('excerpt') or {}).get('text', '')}"
        host = (urlsplit((lead.get("capture") or {}).get("source_url") or "").hostname or "").lower()
        terms = {m.group(0).lower() for m in WATCHLIST_TERMS.finditer(text)}
        terms |= {m.group(0) for m in WATCHLIST_ACRONYMS.finditer(text)}
        if any(host == h or host.endswith("." + h) for h in WATCHLIST_HOSTS):
            terms.add(host)
        if terms:
            clues.append(_clue("K6", "watchlist", [lead["lead_id"]], [lead["direction_id"]],
                               f"Sanctions, PEP, debarment or watchlist mention: {', '.join(sorted(terms))}.",
                               ["K6", lead["lead_id"]], always_shown=True))
    clues.sort(key=clue_order)
    return clues


def clue_order(clue: Mapping[str, Any]) -> tuple:
    return (CLUE_ORDER.index(CLUE_RANK_GROUP[clue["type"]]), clue["source"], clue["clue_id"])


def build_clues(leads_bytes: bytes, leads_doc: Mapping[str, Any], methodology: Mapping[str, Any]) -> dict[str, Any]:
    """The rules' clues."""
    clues = sorted(clue_rules(leads_doc, methodology), key=clue_order)
    return {"schema_version": SCHEMA_VERSION, "generated_at": leads_doc["generated_at"],
            "leads_sha256": checks.sha256_bytes(leads_bytes), "clues": clues}


# ------------------------------------------------------------------ ranking
def _position(order: tuple[str, ...], value: Any) -> int:
    return order.index(value) if value in order else len(order)


def _identity_position(value: Any) -> int:
    """Same-subject leads first, then leads triage did not place, then
    circumstantial leads (about another subject or event): kept and ranked,
    never set aside, and still lifted by any clue they carry."""
    if value in rr.CIRCUMSTANTIAL_IDENTITIES:
        return len(IDENTITY_ORDER) + 1
    return _position(IDENTITY_ORDER, value)


def rank(leads_doc: Mapping[str, Any], clues_doc: Mapping[str, Any] | None,
         triage_doc: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """The Research page's lead order. No question asks how important a lead
    is: clue type, then identity (circumstantial leads after the rest; only
    Jev answers Q1, so with the local model this tier is empty),
    then source, then assertion (triage labels, counted only when both orders
    agreed), then more independent source families (S2), then stored pages
    before snippets (S1)."""
    entries = {entry["lead"]["lead_id"]: entry["lead"] for entry in leads_doc.get("leads") or []}
    triage = {r["lead_id"]: r for r in (triage_doc or {}).get("leads") or [] if isinstance(r, Mapping)}
    declined = {tuple(sorted(c.get("lead_ids") or [])) for c in (triage_doc or {}).get("conflicts") or []
                if (c.get("answer") or {}).get("counted") and not c.get("clue")}
    clue_types: dict[str, set[str]] = {lead_id: set() for lead_id in entries}
    families: dict[str, int] = {}
    for clue in (clues_doc or {}).get("clues") or []:
        ids = [i for i in clue.get("lead_ids") or [] if i in entries]
        if clue.get("source") == "K2" and tuple(sorted(ids)) in declined:
            continue  # the conflict question said these two do not conflict
        group = [rr.parse_lead(entries[i]) for i in ids if not entries[i].get("absence")]
        count = rr.source_families(group) if group else 0
        for lead_id in ids:
            clue_types[lead_id].add(CLUE_RANK_GROUP[clue["type"]])
            families[lead_id] = max(families.get(lead_id, 0), count)
    for lead_id, result in triage.items():
        for kind in result.get("clues") or []:
            if lead_id in clue_types and kind in CLUE_RANK_GROUP:
                clue_types[lead_id].add(CLUE_RANK_GROUP[kind])
    ranked = []
    for lead_id, lead in entries.items():
        labels = (triage.get(lead_id) or {}).get("labels") or {}
        best = min((CLUE_ORDER.index(t) for t in clue_types[lead_id]), default=len(CLUE_ORDER))
        own = 0 if lead.get("absence") else 1
        key = (best, _identity_position(labels.get("identity")), _position(SOURCE_ORDER, labels.get("source")),
               _position(ASSERTION_ORDER, labels.get("assertion")), -max(families.get(lead_id, 0), own),
               {"page": 0, "snippet": 1}.get(lead.get("stored"), 2), lead_id)
        ranked.append({"lead_id": lead_id, "clues": sorted(clue_types[lead_id], key=CLUE_ORDER.index),
                       "source_families": max(families.get(lead_id, 0), own), "snippet_only": lead.get("stored") == "snippet",
                       "_key": key})
    ranked.sort(key=lambda r: r.pop("_key"))
    return ranked


# ------------------------------------------------------------------ CLI
def serialize(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def read_json(path: Path, required: bool = True) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise MergeRefused(f"{path.name} is missing") from None
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MergeRefused(f"{path.name} is unreadable: {exc}") from None


def run(case: Path, round_number: int | None) -> list[str]:
    methodology = read_json(case / "data" / "methodology.json")
    leads_doc, report = build_leads(case, methodology, round_number)
    leads_bytes = serialize(leads_doc)
    clues_bytes = serialize(build_clues(leads_bytes, leads_doc, methodology))
    try:
        with transaction(case) as descriptor:
            atomic_write_files(case, {"data/leads.json": leads_bytes, "data/clues.json": clues_bytes},
                               data_descriptor=descriptor)
    except OrchestrationError as exc:
        raise MergeRefused(f"could not write the merge: {exc}") from None
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--round", type=int, dest="round_number")
    parser.add_argument("--rank", action="store_true", help="print the Research page's lead order")
    args = parser.parse_args(argv)
    case = args.case_dir.resolve()
    try:
        if args.rank:
            data = case / "data"
            print(json.dumps(rank(read_json(data / "leads.json"), read_json(data / "clues.json", False),
                                  read_json(data / "research-triage.json", False)), indent=2, ensure_ascii=False))
            return 0
        report = run(case, args.round_number)
    except MergeRefused as exc:
        print(f"research-merge: {exc}; nothing written", file=sys.stderr)
        return 2
    print(json.dumps({"written": ["data/leads.json", "data/clues.json"], "report": report}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
