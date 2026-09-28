#!/usr/bin/env python3
"""Compute decision-model review signals for a case (the only networked step).

    python3 scripts/decision-signals.py CASE_DIR --phase gate1|report|ingest \
        [--config .spotlight-config.json] [--case-opt-in] [--check] [--dry-run] \
        [--env-file FILE] [--entities FILE] [--existing-claims FILE]

Asks the decision model (OpenRouter Decisions API, default typesafe/jev-1.13,
zero data retention) typed questions. Gate 1 and report results go to
data/decision-signals.json; ingest results go to data/decision-signals-ingest.json.
Signals are review aids: they can lower confidence, add flags or route records
to a lead layer, and never raise confidence or set a verdict. See
integrations/decisions/.

Requests are made only when integrations.decisions.enabled is true in the
config, sensitive mode is off, an OpenRouter key is found (OPENROUTER_API_KEY
in the environment, in integrations.decisions.env_file, or in the checkout
.env) and the case has opted in (first run with --case-opt-in after the user
agreed). --check reports readiness without any request; --dry-run prints the
states that would be sent without any request.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(ROOT / "integrations" / "decisions"))

import decision_signals_lib as lib  # noqa: E402
import questions  # noqa: E402
import rules  # noqa: E402
from client import KEY_ENV, DecisionUnavailable, JevOpenRouterProvider, answer_problem  # noqa: E402

PHASES = ("gate1", "report", "ingest")
INGEST_GROUPS = ("propositions", "memberships", "entities", "matches")
MAX_MATCH_PAIRS = 60


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_sensitive() -> bool:
    agents = ROOT / "AGENTS.md"
    if not agents.is_file():
        return False
    head = agents.read_text(encoding="utf-8").split("---", 2)
    return len(head) > 2 and re.search(r"^sensitive:\s*true\s*$", head[1], re.M) is not None


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines without exporting anything or echoing values."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values


def resolve_key(cfg: dict[str, Any], env_file: Path | None) -> tuple[str, str]:
    """Return (key, where): environment first, then the configured env file, then the checkout .env."""
    if os.environ.get(KEY_ENV):
        return os.environ[KEY_ENV], "environment"
    for candidate in (env_file, cfg.get("env_file"), ROOT / ".env"):
        if not candidate:
            continue
        path = Path(str(candidate)).expanduser()
        key = read_env_file(path).get(KEY_ENV, "")
        if key:
            return key, str(path)
    return "", ""


def settings(config: dict[str, Any]) -> dict[str, Any]:
    block = (config.get("integrations") or {}).get("decisions") or {}
    modes = block.get("modes") or {}
    return {
        "enabled": block.get("enabled") is True,
        "env_file": block.get("env_file"),
        "model": block.get("model") or "typesafe/jev-1.13",
        "modes": {phase: modes.get(phase, "advisory") if modes.get(phase, "advisory") in lib.MODES else "advisory" for phase in PHASES},
    }


def empty_document(model: str) -> dict[str, Any]:
    return {
        "schema_version": lib.SCHEMA_VERSION,
        "generator": "scripts/decision-signals.py",
        "opt_in": None,
        "provider": {"name": "openrouter-decisions", "model_requested": model, "models_served": [], "zero_data_retention": True},
        "question_bank_version": questions.QUESTION_BANK_VERSION,
        "phases": {},
        "overrides": [],
        "usage": {"requests": 0, "input_tokens": 0, "cost_usd": 0.0},
    }


class Asker:
    """Runs provider calls concurrently; any unavailable or malformed answer yields None, never a guess."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.usage = {"requests": 0, "input_tokens": 0, "cost_usd": 0.0}
        self.models: list[str] = []

    def ask_many(self, jobs: list[tuple[str, Any, dict[str, Any]]]) -> dict[str, dict[str, Any] | None]:
        def one(job):
            key, state, qs = job
            try:
                response = self.provider.decide(state, qs)
            except DecisionUnavailable:
                return key, None
            answers = response.get("answers") if isinstance(response, dict) else None
            if not isinstance(answers, dict) or any(answer_problem(q, answers.get(qid)) for qid, q in qs.items()):
                return key, None
            return key, response

        results: dict[str, dict[str, Any] | None] = {}
        with cf.ThreadPoolExecutor(4) as pool:
            for key, response in pool.map(one, jobs):
                results[key] = response
                if response:
                    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
                    tokens, cost = usage.get("input_tokens"), usage.get("cost")
                    self.usage["requests"] += 1
                    self.usage["input_tokens"] += tokens if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0 else 0
                    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
                        self.usage["cost_usd"] = round(self.usage["cost_usd"] + float(cost), 8)
                    served = response.get("model")
                    if isinstance(served, str) and served and served not in self.models:
                        self.models.append(served)
        return results


# ------------------------------------------------------------------ gate1
def gate1(case: Path, asker: Asker, mode: str) -> dict[str, Any]:
    findings = lib.case_findings(case)
    entries: dict[str, dict[str, Any]] = {}
    claims: dict[str, str] = {}
    jobs = []
    for finding in findings:
        fid = lib.text(finding.get("id"))
        hashed, sources, location = lib.grounding_state(case, finding)
        entries[fid] = {"finding_id": fid, "input_sha256": lib.canonical_sha256(hashed), "evidence_location": location}
        claims[fid] = lib.text(finding.get("claim"))
        if location in ("not_found", "no_evidence"):
            reason = "evidence_not_located" if location == "not_found" else "no_quoted_evidence"
            entries[fid].update({"status": "routed", "route_reason": reason, "cap": "none",
                                 "flags": [{"reason": reason, "detail": "The quoted evidence was not found in any stored case source; the existing fact-check process decides."}]})
            continue
        state = {"claim": claims[fid], "evidence": lib.text(finding.get("evidence")), "sources": sources}
        jobs.append((fid, state, questions.ALL["grounding"]))
    for fid, response in asker.ask_many(jobs).items():
        if response is None:
            entries[fid].update({"status": "unavailable", "flags": []})
        else:
            entries[fid].update({**rules.grounding_signal(claims[fid], response["answers"]), "answers": response["answers"]})
    ordered = list(entries.values())
    return phase_header(mode, ordered) | {"findings": ordered}


# ------------------------------------------------------------------ report
def report(case: Path, asker: Asker, mode: str, stored: dict[str, Any] | None) -> dict[str, Any]:
    items = lib.report_items(case, stored)
    responses = asker.ask_many([(item["target"], item["state"], questions.ALL["report"]) for item in items])
    out = []
    for item in items:
        record = {"target": item["target"], "finding_ids": item["finding_ids"], "input_sha256": item["input_sha256"]}
        response = responses.get(item["target"])
        if response is None:
            record.update({"status": "unavailable", "flags": []})
        else:
            record.update({**rules.report_signal(item["state"]["verdict"], response["answers"]), "answers": response["answers"]})
        out.append(record)
    return phase_header(mode, out) | {"items": out}


# ------------------------------------------------------------------ ingest
def ingest(case: Path, asker: Asker, entities_file: Path | None, existing_file: Path | None) -> dict[str, list[dict[str, Any]]]:
    """Return only the groups computed in this run; the caller merges them with earlier groups."""
    entries = lib.batch_items(case)
    computed: set[str] = {"propositions", "memberships"} if lib.read_case_json(case, "knowledge-batch.json") is not None else set()
    if entities_file:
        computed.add("entities")
        entries += lib.entity_items(lib.read_case_json(case, lib.case_input_name(case, entities_file), expect=list, required=True))
    if existing_file:
        computed.add("matches")
        entries += lib.match_items(case, lib.read_case_json(case, lib.case_input_name(case, existing_file), expect=list, required=True), MAX_MATCH_PAIRS)
    jobs, derive = [], {}
    for index, entry in enumerate(entries):
        group, inputs = entry["group"], entry["rule_inputs"]
        if entry.get("unresolved"):
            continue  # never sent: the record's finding or endpoints are missing from the batch
        if group == "propositions":
            derive[index], qs = rules.proposition_signal, questions.ALL["proposition"]
        elif group == "memberships":
            derive[index], qs = (lambda a, d=inputs["declared_relation"]: rules.membership_signal(d, a)), questions.ALL["membership"]
        elif group == "entities":
            derive[index], qs = (lambda a, d=inputs["declared_type"]: rules.entity_signal(a, d)), questions.ALL["entities"]
        else:
            derive[index], qs = rules.match_signal, questions.ALL["matching"]
        jobs.append((str(index), entry["state"], qs))
    responses = asker.ask_many(jobs)
    groups: dict[str, list[dict[str, Any]]] = {group: [] for group in computed}
    for index, entry in enumerate(entries):
        # Every input that can change the derived result is recorded and fingerprinted.
        item = {"target": entry["target"], "input": {**entry["state"], **entry["rule_inputs"]}, "input_sha256": entry["input_sha256"]}
        response = responses.get(str(index))
        if entry.get("unresolved"):
            item.update({"status": "routed", "result": "unresolved", "flags": [{"reason": "unresolved_reference",
                         "detail": "the batch record's finding, claim or event version could not be resolved"}]})
        elif response is None:
            item.update({"status": "unavailable", "result": "", "flags": []})
        else:
            item.update({**derive[index](response["answers"]), "answers": response["answers"]})
        groups[entry["group"]].append(item)
    return groups


def phase_header(mode: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    unavailable = sum(1 for i in items if i.get("status") == "unavailable")
    status = "complete" if not unavailable else ("unavailable" if unavailable == len(items) else "partial")
    header = {"generated_at": now(), "mode": mode, "status": status}
    if unavailable:
        header["reason"] = f"{unavailable} request(s) unavailable; affected items carry no signal"
    return header


def merge_usage(document: dict[str, Any], asker: Asker) -> None:
    usage = document["usage"]
    usage["requests"] += asker.usage["requests"]
    usage["input_tokens"] += asker.usage["input_tokens"]
    usage["cost_usd"] = round(usage["cost_usd"] + asker.usage["cost_usd"], 8)
    for model in asker.models:
        if model not in document["provider"]["models_served"]:
            document["provider"]["models_served"].append(model)


# ------------------------------------------------------------------ main
def main(argv: list[str] | None = None, provider: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case_dir")
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--config", default=str(ROOT / ".spotlight-config.json"))
    parser.add_argument("--case-opt-in", action="store_true", help="record that the user agreed to decision checks for this case")
    parser.add_argument("--dry-run", action="store_true", help="print the states that would be sent and exit")
    parser.add_argument("--check", action="store_true", help="report whether a run would make requests, without any request")
    parser.add_argument("--env-file", type=Path, help=f"file holding {KEY_ENV} (overrides integrations.decisions.env_file)")
    parser.add_argument("--sensitive", action="store_true")
    parser.add_argument("--entities", type=Path, help="entity list [{name, context, type}]; must be a file directly inside CASE_DIR/data/")
    parser.add_argument("--existing-claims", type=Path, help="existing claims [{id, claim}]; must be a file directly inside CASE_DIR/data/")
    args = parser.parse_args(argv)

    case = Path(args.case_dir).resolve()
    config_path = Path(args.config)
    config = load_json(config_path) if config_path.is_file() else {}
    cfg = settings(config)
    sensitive = args.sensitive or config.get("sensitive") is True or manifest_sensitive() or os.environ.get("SPOTLIGHT_SENSITIVE", "").lower() == "true"

    def refuse(reason: str) -> int:
        print(json.dumps({"ran": False, "phase": args.phase, "reason": reason}))
        return 0

    if not lib.ANCHORED_READS:
        if args.check:
            print(json.dumps({"ready": False, "blockers": ["this platform lacks descriptor-anchored file reads"],
                              "key_source": None, "model": cfg["model"], "mode": cfg["modes"][args.phase]}))
            return 0
        return refuse("decision checks need descriptor-anchored file reads, unavailable on this platform")
    try:
        with lib.pinned_case(case) as pinned:
            return run(args, pinned, cfg, sensitive, provider, refuse)
    except lib.SignalsError as exc:
        print(json.dumps({"ran": False, "phase": args.phase, "error": str(exc)}))
        return 2


def run(args: argparse.Namespace, case: Path, cfg: dict[str, Any], sensitive: bool, provider: Any, refuse) -> int:
    """Everything that reads the case, asks the provider or writes signals, under one pinned case root."""
    try:
        main_doc = lib.load_signals(case)
        ingest_doc = lib.load_signals(case, lib.INGEST_SIGNALS_NAME)
    except lib.SignalsError as exc:
        print(json.dumps({"ran": False, "phase": args.phase, "error": str(exc)}))
        return 2
    recorded_opt_in = next((d["opt_in"] for d in (main_doc, ingest_doc) if d and lib.valid_opt_in(d.get("opt_in"))), None)
    opted_in = recorded_opt_in is not None

    if args.check:
        key, where = resolve_key(cfg, args.env_file)
        blockers = [reason for reason, blocked in (
            ("sensitive mode", sensitive),
            ("integrations.decisions.enabled is not true", not cfg["enabled"]),
            (f"{KEY_ENV} not found", not key),
            ("case has not opted in", not opted_in and not args.case_opt_in),
        ) if blocked]
        print(json.dumps({"ready": not blockers, "blockers": blockers, "key_source": where or None,
                          "model": cfg["model"], "mode": cfg["modes"][args.phase]}))
        return 0
    if sensitive:
        return refuse("sensitive mode blocks decision-model egress")
    if not cfg["enabled"] and provider is None:
        return refuse("integrations.decisions.enabled is not true in the config")
    if not opted_in and not args.case_opt_in:
        return refuse("case has not opted in; rerun with --case-opt-in after the user agrees")

    if args.dry_run:
        class Recorder:
            model = cfg["model"]

            def decide(self, state, qs):
                print(json.dumps({"state": state, "question_ids": sorted(qs)}, ensure_ascii=False))
                raise DecisionUnavailable("dry run")
        provider = Recorder()
    elif provider is None:
        key, _where = resolve_key(cfg, args.env_file)
        try:
            provider = JevOpenRouterProvider(key, model=cfg["model"], sensitive=sensitive)
        except DecisionUnavailable as exc:
            return refuse(str(exc))

    asker = Asker(provider)
    mode = cfg["modes"][args.phase]
    try:
        if args.phase == "gate1":
            result = gate1(case, asker, mode)
        elif args.phase == "report":
            result = report(case, asker, mode, main_doc)
        else:
            groups = ingest(case, asker, args.entities, args.existing_claims)
    except lib.SignalsError as exc:  # unsafe or unreadable case input: nothing is sent or written
        print(json.dumps({"ran": False, "phase": args.phase, "error": str(exc)}))
        return 2
    if args.dry_run:
        return 0

    opt_in = recorded_opt_in or {"by": "user", "at": now()}

    def base(current: dict[str, Any] | None) -> dict[str, Any]:
        doc = current or empty_document(cfg["model"])
        doc["opt_in"] = doc.get("opt_in") or opt_in
        doc["question_bank_version"] = questions.QUESTION_BANK_VERSION
        merge_usage(doc, asker)
        return doc

    try:
        if args.phase in ("gate1", "report"):
            def mutate(current):
                doc = base(current)
                doc["phases"][args.phase] = result
                return doc
            lib.update_signals(case, lib.SIGNALS_NAME, mutate)
            items = result.get("findings") or result.get("items") or []
            status = result["status"]
        else:
            # The ingest file carries its own opt-in record; the main file (a hashed
            # report input) is never created or touched by an ingest run.
            def mutate_ingest(current):
                doc = base(current)
                previous = doc["phases"].get("ingest") or {}
                phase = {group: previous[group] for group in INGEST_GROUPS if group in previous}
                phase["groups_generated_at"] = dict(previous.get("groups_generated_at") or {})
                for group, entries in groups.items():
                    phase[group] = entries
                    phase["groups_generated_at"][group] = now()
                every = [i for g in INGEST_GROUPS for i in phase.get(g, [])]
                doc["phases"]["ingest"] = phase_header(mode, every) | phase
                return doc
            lib.update_signals(case, lib.INGEST_SIGNALS_NAME, mutate_ingest)
            items = [i for entries in groups.values() for i in entries]
            status = phase_header(mode, items)["status"]
    except lib.SignalsError as exc:
        print(json.dumps({"ran": False, "phase": args.phase, "error": str(exc)}))
        return 2
    print(json.dumps({
        "ran": True, "phase": args.phase, "mode": mode, "status": status,
        "items": len(items), "flagged": sum(1 for i in items if i.get("flags") and i.get("status") == "judged"),
        "routed": sum(1 for i in items if i.get("status") == "routed"),
        "unavailable": sum(1 for i in items if i.get("status") == "unavailable"),
        "cost_usd_this_run": asker.usage["cost_usd"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
