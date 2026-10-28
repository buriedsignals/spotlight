#!/usr/bin/env python3
"""Ask the decision model whether Anomaly should run on a case's tables.

    scripts/spotlight-python scripts/anomaly-decision.py CASE_DIR [--config FILE]
    scripts/spotlight-python scripts/anomaly-decision.py CASE_DIR --task-block

Run by the Pi harness when a PLANNING investigator finishes, before the
validators. Small models rarely plan Anomaly themselves (LFM3 and the base
12B on Osaurus, 7513e421), so the harness asks the configured decision model
one typed question. Finding the candidate tables is mechanical (.csv, .jsonl
and .parquet files under research/ and evidence/); whether Anomaly would help
answer the brief is the model's judgment.

When the answer's probability reaches THRESHOLD and the plan has no anomaly
section, one is written ("any_table", with the reason); the member still
approves it on the Methodology page. Every outcome is recorded in
data/anomaly-decision.json and printed as one log line. Anything that cannot
ask (no tables, no Anomaly install, no endpoint, a cloud endpoint without the
decision checks switched on or without the case's opt-in, sensitive mode on
a cloud endpoint) changes nothing.

A decision endpoint on a literal loopback address (d1 on this computer) sends
nothing off the machine and costs nothing, so it needs neither the decision
checks switch nor the case's opt-in, and sensitive mode allows it. It gets up
to SAMPLE_ROWS sample rows per table; a cloud endpoint gets headers only.

--task-block prints the ANOMALY block the harness appends to EXECUTION tasks
once the approved plan carries the section this script wrote.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "integrations" / "decisions"))

from integrations import _credentials as credentials  # noqa: E402
from integrations.anomaly import adapter as anomaly  # noqa: E402
from integrations.decisions import client  # noqa: E402

DECISION_FILE = "data/anomaly-decision.json"
THRESHOLD = 0.5
MAX_TABLES = 8
SAMPLE_ROWS = 5
CELL_CHARS = 80
TABLE_DIRS = ("research", "evidence")
QUESTION_KEY = "anomaly_helps"
QUESTION = {
    "type": "noul",
    "instructions": (
        "The state holds an investigation's brief, its planned questions, and the data tables saved in the case "
        "(each with its column names). Anomaly detection runs statistical detectors over a table's rows to flag "
        "records that stand out: outlying amounts, duplicates, gaps, unusual categories. Would running anomaly "
        "detection on one or more of these tables help answer the brief's questions?"
    ),
    "criteria": {"true": "Yes.", "false": "No."},
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def candidate_tables(case: Path) -> list[str]:
    """Table files in the case, relative to it, in a stable order."""
    found = []
    for folder in TABLE_DIRS:
        base = case / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in anomaly.FORMATS:
                found.append(path.relative_to(case).as_posix())
    return found[:MAX_TABLES]


def _clip(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= CELL_CHARS else text[: CELL_CHARS - 1] + "…"


def table_preview(case: Path, relative: str, samples: int) -> dict[str, Any]:
    """Column names and up to `samples` rows, each cell clipped. Parquet gives
    no preview here. The state shape is the one d1 was probed with (tools-46,
    2026-10-28): {"path", "header", "sample_rows"}."""
    path = case / relative
    entry: dict[str, Any] = {"path": relative, "columns": []}
    try:
        if path.suffix.lower() == ".csv":
            with path.open(encoding="utf-8", errors="replace", newline="") as handle:
                reader = csv.reader(handle)
                entry["columns"] = next(reader, [])
                if samples:
                    entry["sample_rows"] = [row for _, row in zip(range(samples), reader)]
        elif path.suffix.lower() == ".jsonl":
            rows = []
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.strip():
                        rows.append(json.loads(line))
                    if len(rows) > samples:
                        break
            first = rows[0] if rows and isinstance(rows[0], dict) else {}
            entry["columns"] = list(first)
            if samples:
                entry["sample_rows"] = [row for row in rows[:samples] if isinstance(row, dict)]
    except (OSError, ValueError, csv.Error):
        pass
    return entry


def _texts(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value] if value else []
    out = []
    for item in items:
        text = item if isinstance(item, str) else (item.get("question") or item.get("direction") or item.get("title")) if isinstance(item, dict) else None
        if isinstance(text, str) and text.strip():
            out.append(text.strip()[:400])
    return out[:12]


def question_state(case: Path, methodology: dict[str, Any], tables: list[str], local: bool) -> dict[str, Any]:
    """The JSON state d1 was trained on: fixed keys, small."""
    brief = (case / "brief-directions.txt").read_text(encoding="utf-8", errors="replace").strip() if (case / "brief-directions.txt").is_file() else ""
    lines = [line.strip() for line in brief.splitlines() if line.strip()]
    questions = [line for line in lines if line.endswith("?")]
    premise = " ".join(line for line in lines if line not in questions)[:1500]
    planned = _texts(methodology.get("questions")) or _texts(methodology.get("directions")) or _texts(methodology.get("brief_directions"))
    previews = []
    for table in tables:
        preview = table_preview(case, table, SAMPLE_ROWS if local else 0)
        entry = {"path": preview["path"], "header": [_clip(column) for column in preview["columns"]]}
        if local:
            rows = preview.get("sample_rows") or []
            entry["sample_rows"] = [[_clip(cell) for cell in (row.values() if isinstance(row, dict) else row)] for row in rows]
        previews.append(entry)
    state: dict[str, Any] = {"brief": {"premise": premise, "questions": questions or lines[:3]},
                             "methodology": {"questions": planned}, "tables": previews}
    if not local:
        state["note"] = "Column names only: no rows leave this computer."
    return state


def opted_in(case: Path) -> bool:
    document = read_json(case / "data" / "decision-signals.json")
    opt_in = document.get("opt_in") if isinstance(document, dict) else None
    return isinstance(opt_in, dict) and bool(opt_in.get("by")) and bool(opt_in.get("at"))


def record(case: Path, outcome: dict[str, Any]) -> None:
    path = case / DECISION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"schema_version": 1, "at": now(), **outcome}, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    print(f"anomaly-decision: {outcome['path']}" + (f" p={outcome['probability']:.3f}" if "probability" in outcome else "")
          + (f" ({outcome['reason']})" if outcome.get("reason") else ""))


def decide(case: Path, config: dict[str, Any], sensitive: bool, env: dict[str, str]) -> dict[str, Any]:
    methodology = read_json(case / "data" / "methodology.json")
    if not isinstance(methodology, dict):
        return {"path": "skipped", "reason": "no methodology.json"}
    if anomaly.plan_domains(methodology):
        return {"path": "skipped", "reason": "the plan already has an anomaly section"}
    tables = candidate_tables(case)
    if not tables:
        return {"path": "skipped", "reason": "no table files in research/ or evidence/"}
    if not anomaly.state().get("available"):
        return {"path": "skipped", "reason": "Anomaly is not installed", "tables": tables}
    block = (config.get("integrations") or {}).get("decisions") or {}
    try:
        url, key_env = client.endpoint_from_config(block.get("endpoint"))
    except client.DecisionUnavailable as exc:
        return {"path": "unavailable", "reason": str(exc), "tables": tables}
    local = client.is_local(url)
    if not local:
        if sensitive:
            return {"path": "cloud-skipped", "reason": "sensitive mode keeps the question on this computer", "tables": tables}
        if block.get("enabled") is not True:
            return {"path": "cloud-skipped", "reason": "decision checks are off", "tables": tables}
        if not opted_in(case):
            return {"path": "cloud-skipped", "reason": "the case has not opted in to decision checks", "tables": tables}
    state = question_state(case, methodology, tables, local)
    try:
        provider = client.JevOpenRouterProvider(env.get(key_env, "") if key_env else "", model=block.get("model") or client.DEFAULT_MODEL,
                                                sensitive=sensitive, url=url)
        response = provider.decide(state, {QUESTION_KEY: QUESTION})
    except client.DecisionUnavailable as exc:
        return {"path": "unavailable", "reason": str(exc), "tables": tables}
    probability = float(response["answers"][QUESTION_KEY]["noul"])
    outcome = {"path": "local-asked" if local else "cloud-asked", "probability": probability, "threshold": THRESHOLD,
               "model": response.get("model") or provider.model, "tables": tables, "decision": probability >= THRESHOLD}
    if outcome["decision"]:
        listed = ", ".join(tables)
        methodology["anomaly"] = {"domains": [{
            "domain": anomaly.ANY_TABLE,
            "sources": [],
            "why": f"Spotlight's decision model judged anomaly detection on the case's tables ({listed}) useful "
                   f"for the brief (p={probability:.2f}). Approve the methodology to run it, or ask for it to be removed.",
        }]}
        path = case / "data" / "methodology.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(methodology, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
    return outcome


def task_block(case: Path) -> str:
    """The ANOMALY block for an EXECUTION task, or "" when there is none to give."""
    decision = read_json(case / DECISION_FILE)
    methodology = read_json(case / "data" / "methodology.json")
    if not (isinstance(decision, dict) and decision.get("decision") is True and isinstance(methodology, dict)
            and anomaly.plan_domains(methodology)):
        return ""
    try:
        catalogue = anomaly.load_catalogue()
    except anomaly.AnomalyUnavailable:
        return ""
    core = [(ref, entry) for ref, entry in sorted(anomaly.runnable(catalogue).items()) if anomaly.is_core(entry)]
    done = {table["path"] for run in anomaly.signal_runs(case) for table in (run.get("tables") or {}).values()
            if isinstance(table, dict)}
    lines = ["ANOMALY: the approved methodology runs Anomaly on the case's tables. For each table below, run at least "
             "one core detector with the anomaly_run tool, mapping each field role to the column that holds it. "
             "The Investigation gate stays closed until every table has a run."]
    for relative in decision.get("tables") or []:
        if relative in done or not (case / relative).is_file():
            continue
        columns = table_preview(case, relative, 0)["columns"]
        lines.append(f"- {relative}: columns {', '.join(columns) if columns else '(read the file to see them)'}")
        for ref, entry in core:
            roles = [field.get("name") for field in entry.get("required_fields") or []]
            # A role named like a column is filled in; every other role is left
            # for the investigator, so the example is never a partial call.
            fields = {role: role if role in columns else "<column>" for role in roles}
            example = json.dumps({"method": ref, "table": relative, "fields": fields})
            lines.append(f"  {entry.get('title') or ref}: needs {', '.join(roles)}. anomaly_run {example}")
    return "\n".join(lines) if len(lines) > 1 else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("case_dir")
    parser.add_argument("--config")
    parser.add_argument("--task-block", action="store_true")
    args = parser.parse_args(argv)
    case = Path(args.case_dir).resolve()
    if args.task_block:
        block = task_block(case)
        if block:
            print(block)
        return 0
    import os
    config = read_json(Path(args.config) if args.config else credentials.config_path()) or {}
    sensitive = os.environ.get("SPOTLIGHT_SENSITIVE") == "true"
    record(case, decide(case, config if isinstance(config, dict) else {}, sensitive, dict(os.environ)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
