#!/usr/bin/env python3
"""Checks D1-D10 of the Research phase, in code.

    scripts/spotlight-python scripts/research_checks.py CHECK CASE_DIR [options]

CHECK is one of d1, d2, d3, d5, d6, d7, d8, d10. Prints {"check", "ok",
"problems"} as JSON and exits 1 when there are problems.

- D1 lane plan (Methodology validation; validate-methodology-navigator.py runs it)
- D2 lane schema and D3 excerpt in file (end of each lane, and again in the merge)
- D4 merge (scripts/research-merge.py)
- D5 budget and D6 coverage (the merge reports them)
- D7 triage invariants: integrations/decisions/research_rules.py, imported here
- D8 the amendment matches the member's marks (after AMENDMENT)
- D9 fact-checker isolation: the Pi harness refuses the spawn (harness/pi/src/roles.ts)
- D10 every pursued direction and clue has a finding or an explicit gap

Fetched text is data: nothing here follows an instruction found in a lead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for _path in (SCRIPT_DIR, ROOT / "integrations" / "decisions"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import research_rules as rr  # noqa: E402
import schema_check  # noqa: E402

SWARM = Path("research") / "swarm"
LANE_FILE, LEADS_FILE, SOURCES = "lane.json", "leads.jsonl", "sources"
LEAD_SCHEMA = ROOT / "schemas" / "research-lead.schema.json"
METHODOLOGY_SCHEMA = ROOT / "schemas" / "methodology.schema.json"
LANE_SCHEMA = ROOT / "schemas" / "research-lane.schema.json"
LANE_CATALOGUE = ROOT / "lanes"
AD_HOC, LOCAL_CORPUS = "ad-hoc", "local-corpus"
CLOUD_ROUTES = {"swarm", "subscription"}
CLOUD_DESTINATIONS = {"openrouter", "subscription_host"}
CASE_BUDGET_CEILING_USD = 5
MAX_ANOMALY_METHODS = 10
CLUE_MARKS = {"pursue", "dismiss"}
DIRECTION_MARKS = {"keep", "drop", "add"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def research_plan(methodology: Mapping[str, Any]) -> Mapping[str, Any] | None:
    plan = methodology.get("research_plan")
    return plan if isinstance(plan, Mapping) else None


def topics(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [t for t in plan.get("topics") or [] if isinstance(t, Mapping)]


def direction_ids(methodology: Mapping[str, Any]) -> list[str]:
    return [d["direction_id"] for d in methodology.get("investigation_plan") or []
            if isinstance(d, Mapping) and isinstance(d.get("direction_id"), str)]


# ------------------------------------------------------------------ D1
def lane_catalogue(root: Path = LANE_CATALOGUE) -> set[str]:
    """Lane type ids: one ``lanes/<id>.yaml`` per catalogue entry, plus ad-hoc."""
    return {path.stem for path in root.glob("*.yaml")} | {AD_HOC} if root.is_dir() else {AD_HOC}


def lane_routes(root: Path = LANE_CATALOGUE) -> dict[str, str]:
    """Each catalogue lane type's route (``swarm`` or ``local``), from its ``route:`` line."""
    routes = {}
    for path in root.glob("*.yaml"):
        found = re.search(r"^route:\s*(\w+)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
        if found:
            routes[path.stem] = found.group(1)
    return routes


# The topic routes each catalogue route allows: web lanes run only on the
# opt-in research swarm (the subscription host's subagents in that mode),
# local lanes only on the local model. An unset topic route takes the lane's.
ALLOWED_ROUTES = {"swarm": {"swarm", "subscription"}, "local": {"local"}}


def _is_cloud(topic: Mapping[str, Any], egress: Iterable[Mapping[str, Any]], lane_route: str | None) -> bool:
    if topic.get("route") in CLOUD_ROUTES or lane_route == "swarm":
        return True
    return any(e.get("destination") in CLOUD_DESTINATIONS for e in egress)


def d1_lane_plan(methodology: Mapping[str, Any], *, catalogue: set[str], sensitive: bool,
                 newsroom: bool, local_model: bool) -> list[str]:
    """D1: the research plan the member is about to approve can run as approved.

    ``local_model``: a model endpoint the member runs is configured. The Pi
    harness always has one; a subscription host without one cannot run the
    local corpus lane or journalist input."""
    plan = research_plan(methodology)
    if plan is None:
        return []
    p = "research_plan"
    # The schema first: the lane runner refuses what it does not allow
    # (an unknown egress destination, consent without swarm, an extra topic field).
    checker = schema_check.load(METHODOLOGY_SCHEMA)
    problems = checker.errors(plan, checker.root["properties"][p], p)
    round_budget, case_budget = plan.get("round_budget_usd"), plan.get("case_budget_usd")
    for name, value in (("round_budget_usd", round_budget), ("case_budget_usd", case_budget)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            problems.append(f"{p}.{name} must be set to a positive amount")
    if isinstance(case_budget, (int, float)) and case_budget > CASE_BUDGET_CEILING_USD:
        problems.append(f"{p}.case_budget_usd is above the ${CASE_BUDGET_CEILING_USD} per-case ceiling")
    if (isinstance(round_budget, (int, float)) and isinstance(case_budget, (int, float))
            and round_budget > case_budget):
        problems.append(f"{p}.round_budget_usd is above the case budget")
    consent = plan.get("consent") if isinstance(plan.get("consent"), Mapping) else None
    if consent is None or not consent.get("decided_by") or not consent.get("decided_at"):
        problems.append(f"{p}.consent must record who decided and when")
        consent = consent or {}
    mode = plan.get("mode")
    if newsroom and mode == "openrouter_swarm":
        problems.append(f"{p}.mode: newsroom mode allows no OpenRouter swarm")

    directions = set(direction_ids(methodology))
    egress = [e for e in plan.get("egress") or [] if isinstance(e, Mapping)]
    keys = [k for k in plan.get("keys") or [] if isinstance(k, Mapping)]
    entries = topics(plan)
    if not entries:
        problems.append(f"{p}.topics must list at least one topic brief")
    seen: set[str] = set()
    methods = 0
    routes = lane_routes()
    for index, topic in enumerate(entries):
        topic_id = str(topic.get("topic_id") or "")
        where = f"{p}.topics[{index}] ({topic_id or 'no id'})"
        if topic_id in seen:
            problems.append(f"{where}: topic id is used twice")
        seen.add(topic_id)
        direction, lane_type = topic.get("direction_id"), topic.get("lane_type")
        if not isinstance(lane_type, str) or not lane_type:
            problems.append(f"{where}: needs a lane type")
            lane_type = ""
        elif lane_type not in catalogue:
            problems.append(f"{where}: lane type {lane_type!r} is not in the lane catalogue (or ad-hoc)")
        if direction not in directions:
            problems.append(f"{where}: direction {direction!r} is not a direction_id of investigation_plan")
        if not re.fullmatch(rf"{re.escape(str(direction))}-{re.escape(lane_type)}(-[0-9]+)?", topic_id):
            problems.append(f"{where}: topic id must be <direction_id>-<lane_type>[-n]")
        questions = topic.get("questions")
        if not isinstance(questions, list) or not any(isinstance(q, str) and q.strip() for q in questions):
            problems.append(f"{where}: needs at least one question")
        if not isinstance(topic.get("token_budget"), int) or topic["token_budget"] < 1:
            problems.append(f"{where}: needs a token budget")
        methods += len(topic.get("anomaly_methods") or [])
        own_egress = [e for e in egress if e.get("topic_id") == topic_id]
        if not own_egress:
            problems.append(f"{where}: egress is not listed; add a {p}.egress entry "
                            f"{{\"topic_id\": \"{topic_id}\", \"destination\": ...}}")
        own_keys = [k.get("name") for k in keys if topic_id in (k.get("topics") or [])]
        if lane_type == AD_HOC and own_keys:
            problems.append(f"{where}: an ad-hoc topic gets no keys")
        lane_route, route = routes.get(lane_type), topic.get("route")
        if lane_route in ALLOWED_ROUTES and route is not None and route not in ALLOWED_ROUTES[lane_route]:
            problems.append(f"{where}: a {lane_type} lane runs on the {lane_route} route, not {route!r}")
        if route == "subscription" and mode != "subscription_subagents":
            problems.append(f"{where}: the subscription route needs mode subscription_subagents")
        cloud = _is_cloud(topic, own_egress, lane_route)
        if cloud and consent.get("swarm") is not True:
            problems.append(f"{where}: a swarm topic needs the member's swarm opt-in")
        if cloud and mode == "local_only":
            problems.append(f"{where}: local_only mode runs no cloud topic")
        if newsroom and (topic.get("route") == "swarm" or any(e.get("destination") == "openrouter" for e in own_egress)):
            problems.append(f"{where}: newsroom mode sends nothing to OpenRouter")
        if sensitive and lane_type != LOCAL_CORPUS:
            problems.append(f"{where}: sensitive mode allows only the local corpus lane")
        local_only = lane_type == LOCAL_CORPUS or topic.get("journalist_input") is True
        if local_only and (cloud or any(e.get("destination") != "local_model" for e in own_egress)):
            problems.append(f"{where}: runs only on the local model, with no search, fetch or cloud egress")
        if local_only and not local_model:
            problems.append(f"{where}: needs a local model endpoint, and none is configured")
        if topic.get("journalist_input") is True:
            if lane_type != LOCAL_CORPUS:
                problems.append(f"{where}: journalist input is read only by the local corpus lane")
            if consent.get("journalist_input") is not True:
                problems.append(f"{where}: journalist input was not granted in the consent record")
    for entry in egress:
        if entry.get("topic_id") not in seen:
            problems.append(f"{p}.egress names an unknown topic {entry.get('topic_id')!r}")
    for key in keys:
        for topic_id in key.get("topics") or []:
            if topic_id not in seen:
                problems.append(f"{p}.keys[{key.get('name')}] names an unknown topic {topic_id!r}")
    if methods > MAX_ANOMALY_METHODS:
        problems.append(f"{p}: {methods} Anomaly methods; at most {MAX_ANOMALY_METHODS} across the plan")
    return problems


def _manifest_sensitive() -> bool:
    """Sensitive mode set in AGENTS.md's frontmatter, read the way decision-signals.py reads it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("spotlight_decision_signals", SCRIPT_DIR / "decision-signals.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return bool(module.manifest_sensitive())


RESEARCH_OFF = "Research is off; turn on Research in the app to add a research plan"
# The harness's reader (harness/pi/src/config.ts researchSwitch) accepts the same values.
SWITCH_ON = {"on", "true", "1", "yes"}
SWITCH_OFF = {"", "off", "false", "0", "no"}


def research_on(config: Mapping[str, Any]) -> bool:
    """Engine's one Research switch. The Pi harness passes it to every Python
    check as SPOTLIGHT_RESEARCH_SWARMS (present, even blank, it decides);
    subscription runtimes have no launcher, so when it is absent the install
    config's ``integrations.research_swarms.enabled`` decides."""
    value = os.environ.get("SPOTLIGHT_RESEARCH_SWARMS")
    if value is not None:
        value = value.strip().lower()
        if value not in SWITCH_ON | SWITCH_OFF:
            raise ValueError('SPOTLIGHT_RESEARCH_SWARMS must be "on" or "off".')
        return value in SWITCH_ON
    integrations = config.get("integrations", {})
    if not isinstance(integrations, Mapping):
        raise ValueError("the install config's integrations must be an object")
    switch = integrations.get("research_swarms", {})
    if not isinstance(switch, Mapping):
        raise ValueError("integrations.research_swarms must be an object")
    enabled = switch.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("integrations.research_swarms.enabled must be true or false")
    return enabled


def d1_from_config(methodology: Mapping[str, Any], config: Mapping[str, Any],
                   local_model: bool | None = None) -> list[str]:
    """D1 with the install's posture: the Research switch, sensitive mode,
    newsroom mode and whether a local model endpoint is configured
    (``local_model`` overrides). With Research off there is no research plan."""
    try:
        research = research_on(config)
    except ValueError as exc:
        return [str(exc)]
    if methodology.get("research_plan") is not None and not research:
        return [RESEARCH_OFF]
    sensitive = (os.environ.get("SPOTLIGHT_SENSITIVE", "").lower() == "true" or config.get("sensitive") is True
                 or _manifest_sensitive())
    newsroom = (config.get("knowledge_destination") or {}).get("mode") == "newsroom"
    if local_model is None:
        triage = ((config.get("integrations") or {}).get("decisions") or {}).get("research_triage") or {}
        local_model = bool(os.environ.get("SPOTLIGHT_MODEL_BASE_URL") or triage.get("local_url"))
    return d1_lane_plan(methodology, catalogue=lane_catalogue(), sensitive=sensitive,
                        newsroom=newsroom, local_model=local_model)


# ------------------------------------------------------------------ D2
class LaneUnreadable(Exception):
    """The lane folder cannot be read safely (a symlink, a missing file)."""


def lane_dir(case: Path, topic_id: str) -> Path:
    return case / SWARM / topic_id


def d2_unapproved_folders(case: Path, plan: Mapping[str, Any]) -> list[str]:
    """Folders under research/swarm/ the approved plan does not name. They are never read."""
    root = case / SWARM
    if not root.is_dir():
        return []
    approved = {str(t.get("topic_id")) for t in topics(plan)}
    return sorted(entry.name for entry in root.iterdir() if entry.name not in approved)


def _no_symlinks(folder: Path) -> None:
    if folder.is_symlink():
        raise LaneUnreadable(f"{folder.name} is a symlink")
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        for name in dirnames + filenames:
            if Path(dirpath, name).is_symlink():
                raise LaneUnreadable(f"{Path(dirpath, name).relative_to(folder)} is a symlink")


def read_lane(case: Path, topic: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]], bytes | None, list[str]]:
    """D2 for one approved topic: (lane.json, lead lines, leads.jsonl bytes, problems).

    Problems mean the lane is failed and none of its leads are merged."""
    topic_id = str(topic.get("topic_id"))
    folder = lane_dir(case, topic_id)
    problems: list[str] = []
    try:
        _no_symlinks(folder)
    except LaneUnreadable as exc:
        return None, [], None, [f"{topic_id}: {exc}"]
    try:
        lane = json.loads((folder / LANE_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [], None, [f"{topic_id}: lane.json is missing"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [], None, [f"{topic_id}: lane.json is unreadable: {exc}"]
    problems += [f"{topic_id}: lane.json {e}" for e in schema_check.load(LANE_SCHEMA).errors(lane)]
    if isinstance(lane, dict):
        for field in ("topic_id", "direction_id", "lane_type"):
            if lane.get(field) != topic.get(field):
                problems.append(f"{topic_id}: lane.json {field} {lane.get(field)!r} differs from the approved brief")
    leads: list[dict[str, Any]] = []
    raw: bytes | None = None
    leads_path = folder / LEADS_FILE
    if leads_path.is_file():
        raw = leads_path.read_bytes()
        lead_schema = schema_check.load(LEAD_SCHEMA)
        ids: set[str] = set()
        for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                lead = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append(f"{topic_id}: leads.jsonl line {number} is not JSON: {exc.msg}")
                continue
            errors = lead_schema.errors(lead)
            if errors:
                problems.append(f"{topic_id}: leads.jsonl line {number}: {errors[0]}")
                continue
            if lead["topic_id"] != topic_id or lead["direction_id"] != topic.get("direction_id"):
                problems.append(f"{topic_id}: leads.jsonl line {number} belongs to another topic or direction")
            if lead["lead_id"] in ids:
                problems.append(f"{topic_id}: leads.jsonl line {number} repeats lead_id {lead['lead_id']}")
            ids.add(lead["lead_id"])
            leads.append(lead)
    return lane if isinstance(lane, dict) else None, leads, raw, problems


# ------------------------------------------------------------------ D3
def normalized(text: str) -> str:
    """Unicode NFC with whitespace runs collapsed: line wrapping in an
    extraction never hides a verbatim quote, and nothing else is forgiven."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def _source_file(folder: Path, ref: Any) -> Path:
    if not isinstance(ref, str) or not ref.startswith(SOURCES + "/") or ".." in Path(ref).parts or "\\" in ref:
        raise LaneUnreadable(f"{ref!r} is not a file under sources/")
    path = folder / ref
    if not path.is_file() or path.is_symlink():
        raise LaneUnreadable(f"{ref} is missing")
    return path


def d3_excerpt(folder: Path, lead: Mapping[str, Any]) -> str | None:
    """D3: why this lead is unverifiable, or None. The excerpt must appear
    verbatim in a stored source whose sha256 matches the lead; a snippet has
    no stored source, so its excerpt cannot be verified."""
    if lead.get("absence"):
        return None
    if lead.get("stored") != "page":
        return "snippet only: no stored source to find the excerpt in"
    try:
        stored = [(None, _source_file(folder, lead.get("content_ref")), lead.get("content_sha256"))]
        for derivative in lead.get("derivatives") or []:
            stored.append((derivative.get("id"), _source_file(folder, derivative.get("content_ref")),
                           derivative.get("content_sha256")))
    except LaneUnreadable as exc:
        return str(exc)
    texts: dict[Any, str] = {}
    for ident, path, expected in stored:
        data = path.read_bytes()
        if sha256_bytes(data) != expected:
            return f"{path.relative_to(folder).as_posix()}: sha256 does not match the lead"
        texts[ident] = normalized(data.decode("utf-8", errors="replace"))
    excerpt = normalized(str((lead.get("excerpt") or {}).get("text") or ""))
    derivative_id = (lead.get("excerpt") or {}).get("derivative_id")
    if derivative_id is not None and derivative_id not in texts:
        return f"excerpt names derivative {derivative_id!r}, which the lead does not carry"
    candidates = [texts[derivative_id]] if derivative_id is not None else list(texts.values())
    if not excerpt or not any(excerpt in text for text in candidates):
        return "excerpt is not verbatim in the stored source"
    return None


# ------------------------------------------------------------------ D5, D6
BUDGET_DIMENSIONS = ("usd", "tokens", "tool_calls", "seconds")


def d5_budget(topic: Mapping[str, Any], lane: Mapping[str, Any]) -> list[str]:
    """D5: a lane that went past its own budget must have stopped (partial or failed)."""
    budget = dict(lane.get("budget") or {})
    if isinstance(topic.get("token_budget"), int):
        budget["tokens"] = min(budget.get("tokens", topic["token_budget"]), topic["token_budget"])
    spend = lane.get("spend") or {}
    over = [d for d in BUDGET_DIMENSIONS
            if isinstance(budget.get(d), (int, float)) and isinstance(spend.get(d), (int, float)) and spend[d] > budget[d]]
    if over and lane.get("status") == "completed":
        return [f"{topic.get('topic_id')}: spent past its budget ({', '.join(over)}) but did not stop"]
    return []


def d6_coverage(direction_list: Iterable[str], leads: Iterable[Mapping[str, Any]],
                lanes: Mapping[str, Mapping[str, Any]], plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """D6: directions with no qualifying lead, each with the queries tried.
    Zero qualifying leads is a valid outcome; the gap is listed, not refused."""
    leads = list(leads)
    gaps = []
    for direction in sorted(set(direction_list)):
        own = [lead for lead in leads if lead.get("direction_id") == direction]
        if any(not lead.get("absence") and lead.get("status") == "candidate" for lead in own):
            continue
        queries = {q for lead in own for q in lead.get("queries_tried") or []}
        for topic in topics(plan):
            if topic.get("direction_id") == direction:
                queries.update((lanes.get(str(topic.get("topic_id"))) or {}).get("queries_tried") or [])
        gaps.append({"direction_id": direction, "queries_tried": sorted(queries)})
    return gaps


# ------------------------------------------------------------------ D7
d7_triage = rr.triage_violations


# ------------------------------------------------------------------ D8
def marks(review: Mapping[str, Any] | None) -> dict[str, str]:
    """The member's Research-page marks: "clue:<id>" -> pursue|dismiss,
    "direction:<id>" -> keep|drop|add (data/research-review.json items)."""
    out: dict[str, str] = {}
    for item in ((review or {}).get("items") or {}).values():
        if not isinstance(item, Mapping):
            continue
        target, final = item.get("target"), item.get("final")
        if not isinstance(target, str) or not isinstance(final, str):
            continue
        kind = target.split(":", 1)[0]
        if (kind == "clue" and final in CLUE_MARKS) or (kind == "direction" and final in DIRECTION_MARKS):
            out[target] = final
    return out


def pursued(marked: Mapping[str, str]) -> set[str]:
    return {t for t, m in marked.items() if m == "pursue" or (t.startswith("direction:") and m in ("keep", "add"))}


def _directions(methodology: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(d.get("direction_id") or d.get("direction")): d
            for d in methodology.get("investigation_plan") or [] if isinstance(d, Mapping)}


def _cites(step: Mapping[str, Any]) -> list[str]:
    cites = step.get("cites")
    return [cites] if isinstance(cites, str) else [c for c in cites or [] if isinstance(c, str)]


def d8_amendment(approved: Mapping[str, Any], amended: Mapping[str, Any], review: Mapping[str, Any] | None) -> list[str]:
    """D8: the amended methodology matches the marks. Every new step cites a
    pursued clue or direction; a direction leaves the plan only when marked
    drop and joins only when marked add; nothing else changed."""
    marked = marks(review)
    allowed = pursued(marked)
    problems: list[str] = []
    for key in sorted(set(approved) | set(amended)):
        if key != "investigation_plan" and approved.get(key) != amended.get(key):
            problems.append(f"{key} changed; an amendment changes only investigation_plan")
    before, after = _directions(approved), _directions(amended)
    for direction in sorted(set(before) - set(after)):
        if marked.get(f"direction:{direction}") != "drop":
            problems.append(f"direction {direction} was dropped but not marked drop")
    for direction in sorted(set(after) - set(before)):
        if marked.get(f"direction:{direction}") != "add":
            problems.append(f"direction {direction} was added but not marked add")
    for target, mark in sorted(marked.items()):
        name = target.split(":", 1)[1]
        if mark == "drop" and name in after:
            problems.append(f"direction {name} was marked drop but is still in the plan")
        if mark == "add" and target.startswith("direction:") and name not in after:
            problems.append(f"direction {name} was marked add but is not in the plan")
    for direction, new in sorted(after.items()):
        old = before.get(direction, {})
        if old and {k: v for k, v in old.items() if k != "steps"} != {k: v for k, v in new.items() if k != "steps"}:
            problems.append(f"direction {direction}: only new steps may change it")
        old_steps = list(old.get("steps") or [])
        for step in old_steps:
            if step not in (new.get("steps") or []):
                problems.append(f"direction {direction}: an approved step was changed or removed")
        for step in new.get("steps") or []:
            if step in old_steps or not isinstance(step, Mapping):
                continue
            if not any(cite in allowed for cite in _cites(step)):
                problems.append(f"direction {direction}: new step {step.get('order')} cites no pursued clue or direction")
    return problems


# ------------------------------------------------------------------ D10
def d10_directions_covered(methodology: Mapping[str, Any], findings: Mapping[str, Any] | None,
                           review: Mapping[str, Any] | None) -> list[str]:
    """D10: every pursued direction and clue has a finding (``research_refs``)
    or an explicit gap (``research_gaps``) in data/findings.json."""
    if research_plan(methodology) is None:
        return []
    wanted = {f"direction:{d}" for d in direction_ids(methodology)}
    wanted |= {t for t, m in marks(review).items() if t.startswith("clue:") and m == "pursue"}
    covered: set[str] = set()
    for finding in (findings or {}).get("findings") or []:
        if isinstance(finding, Mapping):
            covered.update(r for r in finding.get("research_refs") or [] if isinstance(r, str))
    for gap in (findings or {}).get("research_gaps") or []:
        if isinstance(gap, Mapping) and isinstance(gap.get("reason"), str) and gap["reason"].strip():
            covered.add(str(gap.get("ref")))
    return [f"{ref} has no finding and no explicit gap" for ref in sorted(wanted - covered)]


# ------------------------------------------------------------------ CLI
def _json(path: Path, required: bool = True) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise SystemExit(f"research_checks: {path.name} is missing") from None
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"research_checks: {path.name} is unreadable: {exc}") from None


def run(check: str, case: Path, args: argparse.Namespace) -> list[str]:
    data = case / "data"
    methodology = _json(data / "methodology.json")
    review = _json(data / "research-review.json", required=False)
    plan = research_plan(methodology) or {}
    if check == "d1":
        config = _json(Path(args.config), required=False) if args.config else None
        return d1_from_config(methodology, config or {}, args.local_model)
    if check in ("d2", "d3", "d5"):
        problems = []
        if check == "d2":
            problems = [f"{name}: folder not in the approved research plan" for name in d2_unapproved_folders(case, plan)]
        for topic in topics(plan):
            if args.topic and topic.get("topic_id") != args.topic:
                continue
            lane, leads, _raw, lane_problems = read_lane(case, topic)
            if check == "d2":
                problems += lane_problems
            elif check == "d3":
                folder = lane_dir(case, str(topic["topic_id"]))
                problems += [f"{lead['lead_id']}: {reason}" for lead in leads
                             if (reason := d3_excerpt(folder, lead))]
            elif lane is not None:
                problems += d5_budget(topic, lane)
        return problems
    if check == "d6":
        leads_doc = _json(data / "leads.json")
        return [f"{gap['direction_id']}: no qualifying lead" for gap in leads_doc.get("gaps") or []]
    if check == "d7":
        return d7_triage(_json(data / "research-triage.json"))
    if check == "d8":
        return d8_amendment(_json(Path(args.approved)), methodology, review)
    if check == "d10":
        return d10_directions_covered(methodology, _json(data / "findings.json", required=False), review)
    raise SystemExit(f"unknown check {check}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("check", choices=("d1", "d2", "d3", "d5", "d6", "d7", "d8", "d10"))
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--topic", help="d2, d3, d5: only this approved topic")
    parser.add_argument("--approved", help="d8: the approved methodology before the amendment")
    parser.add_argument("--config", help="d1: the install config (sensitive, newsroom, local model)")
    parser.add_argument("--local-model", dest="local_model", action="store_true", default=None,
                        help="d1: a local model endpoint is configured")
    parser.add_argument("--no-local-model", dest="local_model", action="store_false")
    args = parser.parse_args(argv)
    if args.check == "d8" and not args.approved:
        parser.error("d8 needs --approved")
    problems = run(args.check, args.case_dir.resolve(), args)
    print(json.dumps({"check": args.check.upper(), "ok": not problems, "problems": problems}, indent=2, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
