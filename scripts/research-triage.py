#!/usr/bin/env python3
"""Triage a case's research leads with the research questions and rules.

    scripts/spotlight-python scripts/research-triage.py CASE_DIR [--check] [--dry-run]

Reads data/leads.json (the merged leads, schemas/research-leads.schema.json;
each entry's lead also validates against research-lead.schema.json), the
approved methodology (schemas/methodology.schema.json) and, when present,
data/clues.json (schemas/research-clues.schema.json: the K1 and K6 lead ids,
the K2 pairs). Writes data/research-triage.json. Absence leads have no source
to ask about; they are listed, not triaged.

The backend comes from the install config only:
``integrations.decisions.research_triage`` = {"backend": "jev" | "clef" |
"clef-flash" | "local" | "rules", "local_url": "http://127.0.0.1:PORT"}.
Without a ``local_url``, a decision endpoint on loopback (Kev in Ninja mode,
``integrations.decisions.endpoint``) is the local model.
Cloud backends also need ``integrations.decisions.enabled``. Journalist-input
leads and sensitive cases are triaged only by the local tune or by rules; in
newsroom mode cloud backends are off unless the decision endpoint is the
newsroom's own.

Until the decision-model acceptance test passes, triage only flags (see
integrations/decisions/research_rules.py). Output that breaks the triage
invariants (D7) is never written: the script exits 3 and names the breach.
--check reports the routing without a request; --dry-run prints the states
that would be sent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(ROOT / "integrations" / "decisions"))
sys.path.insert(0, str(ROOT))

from integrations import _credentials as credentials  # noqa: E402
import decision_signals_lib as lib  # noqa: E402
import research_backends as backends  # noqa: E402
import research_questions as rq  # noqa: E402
import research_rules as rr  # noqa: E402
from client import DecisionUnavailable, JevOpenRouterProvider, endpoint_from_config, is_local, is_openrouter  # noqa: E402
from spotlight_orchestration.case_writer import atomic_write_files  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.storage import transaction  # noqa: E402

OUTPUT = "research-triage.json"
SCHEMA_VERSION = "1.0"
SCHEMAS = ROOT / "schemas"
LEADS_SCHEMA = SCHEMAS / "research-leads.schema.json"
LEAD_SCHEMA = SCHEMAS / "research-lead.schema.json"
CLUES_SCHEMA = SCHEMAS / "research-clues.schema.json"
METHODOLOGY_SCHEMA = SCHEMAS / "methodology.schema.json"


def _signals_cli():
    spec = importlib.util.spec_from_file_location("spotlight_decision_signals", SCRIPT_DIR / "decision-signals.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ case inputs
def brief_from_methodology(methodology: Mapping[str, Any]) -> rr.TriageBrief:
    """The brief context Q1, Q2 and C1 need, from the approved methodology.

    The premise is the methodology's ``lead``; the directions are the
    investigation plan's ``direction_id``s (labelled with their ``direction``)
    plus any research-plan topic's ``direction_id``. The methodology schema has
    no subjects list, so Q1 compares against the premise alone."""
    premise = methodology.get("lead") if isinstance(methodology.get("lead"), str) else ""
    directions: dict[str, str] = {}
    for item in methodology.get("investigation_plan", []) or []:
        if isinstance(item, Mapping) and isinstance(item.get("direction_id"), str):
            directions[item["direction_id"]] = str(item.get("direction") or item["direction_id"])
    plan = methodology.get("research_plan") if isinstance(methodology.get("research_plan"), Mapping) else {}
    for topic in plan.get("topics", []) or []:
        if isinstance(topic, Mapping) and isinstance(topic.get("direction_id"), str):
            directions.setdefault(topic["direction_id"], topic["direction_id"])
    return rr.TriageBrief(premise=premise, subjects=(), directions=directions)


def rule_clues(clues_doc: Mapping[str, Any] | None) -> tuple[dict[str, set[str]], list[dict[str, Any]]]:
    """Per lead, the rule clues triage acts on (K1, K6); and the K2 pairs for C4.
    Reads a research-clues document: each clue's ``source`` names its rule."""
    per_lead: dict[str, set[str]] = {}
    pairs: list[dict[str, Any]] = []
    for clue in (clues_doc or {}).get("clues", []) or []:
        if not isinstance(clue, Mapping):
            continue
        rule, ids = clue.get("source"), [i for i in clue.get("lead_ids", []) or [] if isinstance(i, str)]
        if rule in ("K1", "K6"):
            for lead_id in ids:
                per_lead.setdefault(lead_id, set()).add(rule)
        elif rule == "K2" and len(ids) == 2 and isinstance(clue.get("attribute"), str):
            pairs.append({"lead_ids": ids, "attribute": clue["attribute"]})
    return per_lead, pairs


def schema_errors(schema: Path, document: Any) -> list[str]:
    import schema_check
    return schema_check.load(schema).errors(document)


# ------------------------------------------------------------------ triage
def questions_for(brief: rr.TriageBrief, mode: str, acceptance: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[str], list[str]]:
    base = dict(rq.QUESTIONS)
    if brief.directions:
        base["q2_direction"] = rq.direction_question(brief.directions)
    noul_ids = rr.on_trial_asked(mode, acceptance)
    for qid in noul_ids:
        base[qid] = rq.ON_TRIAL[qid]
    choice_ids = [qid for qid, q in base.items() if q["type"] == "choice"]
    return rq.with_reversed(base), choice_ids, noul_ids


def brief_state(brief: rr.TriageBrief) -> dict[str, Any]:
    return {"brief": {"premise": brief.premise, "subjects": list(brief.subjects)}}


def run_triage(raw_leads: list[Any], brief: rr.TriageBrief, clues_doc: Mapping[str, Any] | None,
               provider_for: Callable[[str], Any], routing: Callable[[bool], tuple[str, str]],
               acceptance: Mapping[str, Any] | None = rr.ACCEPTANCE) -> dict[str, Any]:
    """Triage every valid lead. ``routing(journalist_input)`` gives (backend,
    reason); ``provider_for(backend)`` gives a provider or None (rules)."""
    mode = rr.triage_mode(acceptance)
    questions, choice_ids, noul_ids = questions_for(brief, mode, acceptance)
    per_lead, pairs = rule_clues(clues_doc)
    leads: dict[str, rr.TriageLead] = {}
    invalid = []
    absence: list[dict[str, str]] = []
    for index, raw in enumerate(raw_leads):
        errors = schema_errors(LEAD_SCHEMA, raw)
        if errors:
            invalid.append({"index": index, "reason": f"not a research lead: {errors[0]}"})
            continue
        if raw["absence"]:
            absence.append({"lead_id": raw["lead_id"], "direction_id": raw["direction_id"]})
            continue
        try:
            lead = rr.parse_lead(raw)
        except rr.LeadInvalid as exc:
            invalid.append({"index": index, "reason": str(exc)})
            continue
        if lead.lead_id in leads:
            invalid.append({"index": index, "reason": "duplicate lead_id"})
            continue
        leads[lead.lead_id] = lead

    def ask(journalist_input: bool, state: dict[str, Any], qs: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
        backend, reason = routing(journalist_input)
        provider = provider_for(backend) if backend != "rules" else None
        if provider is None:
            return {}, "rules", reason or ("" if backend == "rules" else f"{backend} is not configured")
        try:
            return provider.decide(state, qs)["answers"], backend, reason
        except DecisionUnavailable as exc:
            return {}, "rules", f"{backend} unavailable: {exc}"

    results = []
    for lead in leads.values():
        answers, used, reason = ask(lead.journalist_input, {**rr.lead_state(lead), **brief_state(brief)}, questions)
        recorded = rr.record_answers(answers, choice_ids, noul_ids, mode, acceptance)
        outcome = rr.lead_outcome(lead, recorded, mode, per_lead.get(lead.lead_id, ()))
        results.append({**outcome, "backend": used, **({"backend_reason": reason} if reason else {}),
                        "answers": recorded if answers else {}})

    by_id = {r["lead_id"]: r for r in results}
    conflicts = []
    for pair in pairs:
        a, b = (leads.get(i) for i in pair["lead_ids"])
        if a is None or b is None:
            continue
        qs = rq.with_reversed({"c4_conflict": rq.conflict_question(pair["attribute"])})
        state = {"item_a": rr.lead_state(a)["item"], "item_b": rr.lead_state(b)["item"], "attribute": pair["attribute"]}
        answers, used, reason = ask(a.journalist_input or b.journalist_input, state, qs)
        recorded = rr.record_answers(answers, ["c4_conflict"], [], mode, acceptance)["c4_conflict"]
        clue = rr.conflict_outcome(recorded)
        conflicts.append({"lead_ids": pair["lead_ids"], "attribute": pair["attribute"], "backend": used,
                          **({"backend_reason": reason} if reason else {}), "answer": recorded, "clue": clue})
        if clue:
            for lead_id in pair["lead_ids"]:
                by_id[lead_id]["clues"].append(clue)
                if by_id[lead_id]["disposition"] == "keep":
                    by_id[lead_id]["disposition"] = "flag"

    document = {
        "schema_version": SCHEMA_VERSION, "generator": "scripts/research-triage.py",
        "bank_version": rq.RESEARCH_BANK_VERSION, "mode": mode,
        **({"notice": rr.FLAG_ONLY_NOTICE} if mode == rr.FLAG_ONLY else {}),
        "questions_asked": sorted(q for q in questions if not q.endswith("_rev")),
        "leads": results, "conflicts": conflicts, "invalid_leads": invalid, "absence_leads": absence,
    }
    return document


# ------------------------------------------------------------------ install config
def triage_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    block = (config.get("integrations") or {}).get("decisions") or {}
    triage = block.get("research_triage") or {}
    backend = triage.get("backend", "rules")
    if backend not in backends.BACKENDS:
        raise SystemExit(f"integrations.decisions.research_triage.backend must be one of {', '.join(backends.BACKENDS)}")
    try:
        endpoint = endpoint_from_config(block.get("endpoint"))
    except DecisionUnavailable:
        endpoint = ("", "")
    return {"backend": backend, "local_url": triage.get("local_url") or "",
            # A decision endpoint on this computer (Kev in Ninja mode) is the local triage model.
            "local_endpoint": endpoint if is_local(endpoint[0]) else None, "cloud_enabled": block.get("enabled") is True,
            "model": block.get("model") or "typesafe/jev-1.13", "env_file": block.get("env_file")}


def build_providers(cfg: Mapping[str, Any], signals: Any) -> tuple[dict[str, Any], bool, list[str]]:
    """Providers that could be built, whether the decision endpoint is a newsroom's own, and why others could not."""
    providers: dict[str, Any] = {}
    notes: list[str] = []
    newsroom_endpoint = False
    if cfg["local_url"]:
        try:
            providers["local"] = backends.LlamaServerProvider(cfg["local_url"])
        except DecisionUnavailable as exc:
            notes.append(f"local: {exc}")
    elif cfg["local_endpoint"]:
        url, key_env = cfg["local_endpoint"]
        key, _where = signals.resolve_key({"env_file": cfg["env_file"]}, None, key_env)
        providers["local"] = JevOpenRouterProvider(key, model=cfg["model"], url=url)
    if cfg["backend"] == "jev":
        try:
            url, key_env = signals.install_endpoint()
            newsroom_endpoint = not is_openrouter((urlparse(url).hostname or "").lower())
            key, _where = signals.resolve_key({"env_file": cfg["env_file"]}, None, key_env)
            providers["jev"] = JevOpenRouterProvider(key, model=cfg["model"], url=url)
        except DecisionUnavailable as exc:
            notes.append(f"jev: {exc}")
    if cfg["backend"] in ("clef", "clef-flash"):
        account = credentials.credential(backends.CLEF_ACCOUNT_ENV, ROOT)
        token = credentials.credential(backends.CLEF_TOKEN_ENV, ROOT)
        try:
            providers[cfg["backend"]] = backends.ClefWorkersAIProvider(account or "", token or "", model=cfg["backend"])
        except DecisionUnavailable as exc:
            notes.append(f"{cfg['backend']}: {exc}")
    return providers, newsroom_endpoint, notes


def knowledge_mode(config: Mapping[str, Any]) -> str:
    mode = (config.get("knowledge_destination") or {}).get("mode", "solo")
    return mode if mode in ("solo", "newsroom") else "solo"


def write_output(case: Path, document: Mapping[str, Any]) -> None:
    payload = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with transaction(case) as descriptor:
            atomic_write_files(case, {f"data/{OUTPUT}": payload}, data_descriptor=descriptor)
    except OrchestrationError as exc:
        raise SystemExit(f"could not write data/{OUTPUT}: {exc}") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    case = args.case_dir.resolve()
    try:
        config = json.loads(credentials.config_path(ROOT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
    cfg = triage_settings(config)
    signals = _signals_cli()
    sensitive = signals.manifest_sensitive()
    newsroom = knowledge_mode(config) == "newsroom"
    providers, newsroom_endpoint, notes = build_providers(cfg, signals) if not args.check else ({}, False, [])

    def routing(journalist_input: bool) -> tuple[str, str]:
        return backends.route(cfg["backend"], newsroom=newsroom, sensitive=sensitive, journalist_input=journalist_input,
                              local_available="local" in providers or (args.check and bool(cfg["local_url"] or cfg["local_endpoint"])),
                              cloud_enabled=cfg["cloud_enabled"], newsroom_endpoint=newsroom_endpoint)

    if args.check:
        backend, reason = routing(False)
        local_backend, local_reason = routing(True)
        print(json.dumps({"requested": cfg["backend"], "leads": {"backend": backend, "reason": reason},
                          "journalist_input_leads": {"backend": local_backend, "reason": local_reason},
                          "mode": rr.triage_mode(), "knowledge_mode": "newsroom" if newsroom else "solo"}, indent=2))
        return 0

    try:
        leads_doc = lib.read_case_json(case, "leads.json", required=True)
        methodology = lib.read_case_json(case, "methodology.json", required=True)
        clues_doc = lib.read_case_json(case, "clues.json")
    except lib.SignalsError as exc:
        print(f"research-triage: {exc}", file=sys.stderr)
        return 2
    for name, schema, document in (("leads.json", LEADS_SCHEMA, leads_doc), ("methodology.json", METHODOLOGY_SCHEMA, methodology),
                                    ("clues.json", CLUES_SCHEMA, clues_doc)):
        errors = schema_errors(schema, document) if document is not None else []
        if errors:
            print(f"research-triage: data/{name} does not validate: {errors[:3]}", file=sys.stderr)
            return 2
    # Each merged entry carries the kept lane lead; the lead itself is checked per lead in run_triage.
    raw_leads = [entry["lead"] for entry in leads_doc["leads"]]
    brief = brief_from_methodology(methodology)

    if args.dry_run:
        print(json.dumps({"brief": brief_state(brief), "states": [
            rr.lead_state(rr.parse_lead(raw)) for raw in raw_leads if _valid(raw)]}, indent=2, ensure_ascii=False))
        return 0

    document = run_triage(raw_leads, brief, clues_doc, providers.get, routing)
    document["generated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if notes:
        document["backend_notes"] = notes
    violations = rr.triage_violations(document)
    if violations:
        print("research-triage: triage broke its invariants; nothing written:\n  " + "\n  ".join(violations), file=sys.stderr)
        return 3
    write_output(case, document)
    print(f"research-triage: {len(document['leads'])} leads triaged ({document['mode']}), "
          f"{len(document['invalid_leads'])} invalid -> data/{OUTPUT}")
    return 0


def _valid(raw: Any) -> bool:
    if schema_errors(LEAD_SCHEMA, raw) or raw["absence"]:
        return False
    try:
        rr.parse_lead(raw)
    except rr.LeadInvalid:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
