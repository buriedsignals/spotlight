#!/usr/bin/env python3
"""Anomaly clue methods: the pinned catalogue, the methods that fit a topic
brief, and approved methods run on lane tables into Research clues.

    scripts/spotlight-python integrations/anomaly/adapter.py state
    scripts/spotlight-python integrations/anomaly/adapter.py methods

Anomaly (github.com/buriedsignals/Anomaly) stays a separate install, named by
``ANOMALY_HOME`` (its checkout, with its own ``.venv``). Spotlight trusts its
catalogue only when ``detectors/catalogue.json`` carries the hash pinned in
``manifest.json`` and that hash matches the file's content; anything else
hides the feature.

A method is ``method:<detector id>@<version>``. Only detectors that run on one
supplied table (``required_tables == ["*"]``) can serve a lane. A topic brief
names its methods in ``anomaly_methods`` (D1 caps them at 10 per plan). The
lane that collected a table lists it in its folder's ``method-inputs.json``:

    {"schema_version": "1.0",
     "inputs": [{"method": "method:numeric.zscore_outliers@1.1.0",
                 "lead_id": "<the lead whose stored original is the table>",
                 "format": "csv" | "jsonl" | "parquet",
                 "fields": {"<detector field>": "<table column>"}}]}

At merge time each input runs only when its method is approved for that
topic, its lead passed D3 as a stored page and the table's sha256 still
matches the lead. The runner gets a read-only copy of the table in a private
temporary folder, an environment without keys, and the request pinned to the
catalogue's version and implementation hash. Every output line is checked
against the catalogue's signal schema and the method's expected output before
it becomes a clue.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

_CHECKOUT = Path(__file__).resolve().parents[2]
if str(_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(_CHECKOUT))
from integrations._credentials import credential  # noqa: E402

MANIFEST = Path(__file__).with_name("manifest.json")
PIN: Mapping[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))["catalogue_pin"]
HOME_ENV = "ANOMALY_HOME"
METHOD_REF = re.compile(r"^method:(?P<id>[a-z0-9][a-z0-9._-]*)@(?P<version>[0-9A-Za-z][0-9A-Za-z.+-]*)$")
INPUTS_FILE = "method-inputs.json"
FORMATS = {"csv", "jsonl", "parquet"}
MAX_PER_RUN = 10  # Anomaly's one-shot limit, and D1's per-plan limit
RUN_TIMEOUT_SECONDS = 120
MAX_CANDIDATES_SHOWN = 5
CLUE_TYPE = "method_signal"


class AnomalyUnavailable(Exception):
    """No Anomaly install, or one whose catalogue is not the pinned release."""


class MethodRefused(Exception):
    """One method input cannot run, or its output is not what the method declares."""


# ------------------------------------------------------------------ catalogue
def method_ref(entry: Mapping[str, Any]) -> str:
    return f"method:{entry['id']}@{entry['version']}"


def _canonical_hash(body: Mapping[str, Any]) -> str:
    # The rule Anomaly's catalogue.py states: sha256 over the canonical JSON
    # (sorted keys, no spaces) of every key but catalogue_hash.
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def anomaly_home() -> Path:
    value = (credential(HOME_ENV) or "").strip()
    if not value:
        raise AnomalyUnavailable(f"{HOME_ENV} is not set")
    home = Path(value).expanduser()
    if not home.is_absolute():
        raise AnomalyUnavailable(f"{HOME_ENV} must be an absolute path")
    return home


def runner_python(home: Path) -> Path:
    for candidate in (home / ".venv" / "bin" / "python", home / ".venv" / "Scripts" / "python.exe"):
        if candidate.is_file():
            return candidate
    raise AnomalyUnavailable("Anomaly's .venv is missing; run `uv sync` in its checkout")


def load_catalogue(home: Path | None = None, pin: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The installed catalogue, only when it is exactly the pinned release."""
    pin = PIN if pin is None else pin
    home = anomaly_home() if home is None else home
    path = home / "detectors" / "catalogue.json"
    if path.is_symlink() or not path.is_file():
        raise AnomalyUnavailable("detectors/catalogue.json is missing from the Anomaly install")
    try:
        catalogue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnomalyUnavailable(f"the Anomaly catalogue is unreadable: {exc}") from None
    if not isinstance(catalogue, dict) or not isinstance(catalogue.get("detectors"), list):
        raise AnomalyUnavailable("the Anomaly catalogue has no detector list")
    if catalogue.get("schema_version") != pin["schema_version"]:
        raise AnomalyUnavailable(f"catalogue schema {catalogue.get('schema_version')!r} is not the pinned "
                                 f"{pin['schema_version']}")
    if catalogue.get("catalogue_hash") != pin["catalogue_hash"]:
        raise AnomalyUnavailable(f"the installed catalogue is not the pinned release {pin['release'][:12]}")
    body = {key: value for key, value in catalogue.items() if key != "catalogue_hash"}
    if _canonical_hash(body) != pin["catalogue_hash"]:
        raise AnomalyUnavailable("the catalogue's content does not match its pinned hash")
    return catalogue


def state(pin: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Whether the feature is shown, and why not."""
    pin = PIN if pin is None else pin
    try:
        home = anomaly_home()
        load_catalogue(home, pin)
        runner_python(home)
    except AnomalyUnavailable as exc:
        return {"available": False, "reason": str(exc), "release": pin["release"]}
    return {"available": True, "reason": None, "release": pin["release"], "catalogue_hash": pin["catalogue_hash"]}


def runnable(catalogue: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Methods a lane table can serve: detectors that run on one supplied table."""
    return {method_ref(entry): entry for entry in catalogue.get("detectors") or []
            if isinstance(entry, Mapping) and entry.get("required_tables") == ["*"]}


def describe(entry: Mapping[str, Any]) -> dict[str, Any]:
    """What a brief and the Methodology page show for one method."""
    return {
        "method": method_ref(entry),
        "title": entry.get("title"),
        "description": entry.get("description"),
        "required_fields": [field.get("name") for field in entry.get("required_fields") or []],
        "assumptions": list(entry.get("assumptions") or []),
        "false_positives": list(entry.get("false_positives") or []),
    }


def methods() -> list[dict[str, Any]]:
    """The methods that fit a lane, or [] when the feature is hidden."""
    try:
        catalogue = load_catalogue()
    except AnomalyUnavailable:
        return []
    return [describe(entry) for _ref, entry in sorted(runnable(catalogue).items())]


def plan_methods(methodology: Mapping[str, Any]) -> dict[str, list[str]]:
    plan = methodology.get("research_plan")
    topics = plan.get("topics") if isinstance(plan, Mapping) else None
    out: dict[str, list[str]] = {}
    for topic in topics if isinstance(topics, list) else []:
        if isinstance(topic, Mapping) and topic.get("anomaly_methods"):
            out[str(topic.get("topic_id"))] = [str(m) for m in topic["anomaly_methods"]]
    return out


def plan_problems(methodology: Mapping[str, Any]) -> list[str]:
    """Methodology validation: every named method is in the pinned catalogue and
    runs on one table. D1 (scripts/research_checks.py) owns the count."""
    named = plan_methods(methodology)
    if not named:
        return []
    try:
        available = runnable(load_catalogue())
    except AnomalyUnavailable as exc:
        return [f"research_plan names Anomaly methods, but the method catalogue is unavailable ({exc})"]
    return [f"research_plan.topics ({topic_id}): {ref} is not a single-table method in the pinned Anomaly catalogue"
            for topic_id, refs in sorted(named.items()) for ref in refs if ref not in available]


def view(methodology: Mapping[str, Any]) -> dict[str, Any] | None:
    """The Methodology page's data: the methods that fit, and each topic's
    attached methods. None when the feature is hidden and no topic names one."""
    named = plan_methods(methodology)
    try:
        available = runnable(load_catalogue())
    except AnomalyUnavailable as exc:
        if not named:
            return None
        return {"available": False, "reason": str(exc), "methods": [],
                "topics": {topic_id: [{"method": ref, "known": False} for ref in refs] for topic_id, refs in named.items()}}
    return {
        "available": True,
        "release": PIN["release"],
        "methods": [describe(entry) for _ref, entry in sorted(available.items())],
        "topics": {topic_id: [{**describe(available[ref]), "known": True} if ref in available
                              else {"method": ref, "known": False} for ref in refs]
                   for topic_id, refs in named.items()},
    }


# ------------------------------------------------------------------ running
def _runner_env(scratch: Path) -> dict[str, str]:
    """No keys, tokens, proxies or Python hooks reach the runner."""
    env = {"PATH": os.defpath, "HOME": str(scratch), "TMPDIR": str(scratch), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONUTF8": "1"}
    for name in ("SYSTEMROOT", "WINDIR"):  # Windows cannot start Python without them
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


def check_signal(signal: Any, entry: Mapping[str, Any], input_hash: str, schema: Mapping[str, Any]) -> list[str]:
    """Problems with one output line, against the catalogue's signal schema and the method's declaration."""
    if not isinstance(signal, dict):
        return ["an output line is not an object"]
    problems = []
    if set(signal) != set(schema):
        problems.append(f"fields {sorted(set(signal) ^ set(schema))} differ from the signal schema")
    provenance = signal.get("provenance")
    provenance_schema = schema.get("provenance") if isinstance(schema.get("provenance"), Mapping) else {}
    if not isinstance(provenance, dict) or set(provenance) != set(provenance_schema):
        problems.append("provenance differs from the signal schema")
        provenance = provenance if isinstance(provenance, dict) else {}
    for key, declared in schema.items():
        if isinstance(declared, str) and key in signal:
            expected = dict if declared.startswith("object") else str
            if not isinstance(signal[key], expected):
                problems.append(f"{key} is not a{'n object' if expected is dict else ' string'}")
    expected_values = {
        "status": "lead", "detector_id": entry["id"], "detector_version": entry["version"],
        "detector_hash": entry["implementation_hash"], "category": entry.get("signal_category"),
    }
    problems += [f"{key} is {signal.get(key)!r}, not {value!r}" for key, value in expected_values.items()
                 if signal.get(key) != value]
    if provenance.get("input_hash") != input_hash:
        problems.append("provenance.input_hash is not the table that was supplied")
    if provenance.get("approval") != "caller-recorded":
        problems.append("provenance.approval is not caller-recorded")
    values = signal.get("values")
    missing = [column for column in entry.get("expected_output") or []
               if not isinstance(values, dict) or column not in values]
    if missing:
        problems.append(f"values lack the method's expected output {missing}")
    return problems


def run_table(table: Path, fmt: str, requests: list[tuple[Mapping[str, Any], Mapping[str, str]]],
              catalogue: Mapping[str, Any], home: Path) -> dict[str, list[dict[str, Any]]]:
    """Run up to ten methods on one table; signals per method ref, each checked."""
    if not 1 <= len(requests) <= MAX_PER_RUN:
        raise MethodRefused(f"between 1 and {MAX_PER_RUN} methods may run on one table")
    python = runner_python(home)
    data = table.read_bytes()
    input_hash = "sha256:" + hashlib.sha256(data).hexdigest()
    with tempfile.TemporaryDirectory(prefix="spotlight-anomaly-") as scratch_name:
        scratch = Path(scratch_name)
        os.chmod(scratch, 0o700)
        copy = scratch / f"table.{fmt}"
        copy.write_bytes(data)
        os.chmod(copy, stat.S_IRUSR)
        request_file = scratch / "request.json"
        request_file.write_text(json.dumps([
            {"id": entry["id"], "version": entry["version"], "implementation_hash": entry["implementation_hash"],
             "fields": dict(fields)} for entry, fields in requests]), encoding="utf-8")
        os.chmod(request_file, stat.S_IRUSR)
        try:
            proc = subprocess.run(
                [str(python), "-I", "-m", "anomaly.oneshot", "--table", str(copy), "--request", str(request_file),
                 "--approved"],
                cwd=scratch, env=_runner_env(scratch), capture_output=True, text=True, encoding="utf-8",
                timeout=RUN_TIMEOUT_SECONDS, check=False, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise MethodRefused(f"the runner did not finish within {RUN_TIMEOUT_SECONDS} s") from None
        if hashlib.sha256(copy.read_bytes()).hexdigest() != input_hash.removeprefix("sha256:"):
            raise MethodRefused("the runner changed its read-only table copy")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise MethodRefused(f"the runner refused ({proc.returncode}): {detail[-1][:300] if detail else 'no message'}")
    by_id = {entry["id"]: entry for entry, _fields in requests}
    signals: dict[str, list[dict[str, Any]]] = {method_ref(entry): [] for entry, _fields in requests}
    schema = catalogue.get("signal_schema") or {}
    for number, line in enumerate(proc.stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            signal = json.loads(line)
        except json.JSONDecodeError:
            raise MethodRefused(f"output line {number} is not JSON") from None
        entry = by_id.get(signal.get("detector_id")) if isinstance(signal, dict) else None
        if entry is None:
            raise MethodRefused(f"output line {number} names a method that was not requested")
        problems = check_signal(signal, entry, input_hash, schema)
        if problems:
            raise MethodRefused(f"{method_ref(entry)} output line {number}: {problems[0]}")
        signals[method_ref(entry)].append(signal)
    return signals


def _read_inputs(folder: Path) -> list[Any]:
    path = folder / INPUTS_FILE
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise MethodRefused(f"{INPUTS_FILE} is not a regular file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MethodRefused(f"{INPUTS_FILE} is unreadable: {exc}") from None
    if not isinstance(document, dict) or document.get("schema_version") != "1.0" or not isinstance(document.get("inputs"), list):
        raise MethodRefused(f"{INPUTS_FILE} needs schema_version 1.0 and an inputs list")
    return document["inputs"]


def _valid_input(item: Any) -> str | None:
    if not isinstance(item, dict) or set(item) != {"method", "lead_id", "format", "fields"}:
        return "an input needs exactly method, lead_id, format and fields"
    if not isinstance(item["method"], str) or not METHOD_REF.match(item["method"]):
        return "method is not method:<id>@<version>"
    if not isinstance(item["lead_id"], str) or not item["lead_id"]:
        return "lead_id is missing"
    if item["format"] not in FORMATS:
        return f"format is not one of {sorted(FORMATS)}"
    fields = item["fields"]
    if not isinstance(fields, dict) or not all(isinstance(k, str) and isinstance(v, str) and k and v
                                               for k, v in fields.items()):
        return "fields must map detector fields to table columns"
    return None


def _table_lead(leads_doc: Mapping[str, Any], topic_id: str, lead_id: str) -> Mapping[str, Any] | None:
    """The merged lead that holds this lane's lead: itself, or the copy D4 kept."""
    for entry in leads_doc.get("leads") or []:
        lead = entry.get("lead") or {}
        if (lead.get("topic_id"), lead.get("lead_id")) == (topic_id, lead_id):
            return lead
        if any((c.get("topic_id"), c.get("lead_id")) == (topic_id, lead_id) for c in entry.get("copies") or []):
            return lead
    return None


def _table_file(case: Path, lead: Mapping[str, Any]) -> Path:
    if lead.get("absence") or lead.get("stored") != "page" or lead.get("status") != "candidate":
        raise MethodRefused("the table's lead is not a stored page that passed D3")
    ref = lead.get("content_ref")
    if not isinstance(ref, str) or not ref.startswith("sources/") or ".." in Path(ref).parts or "\\" in ref:
        raise MethodRefused("the table is not a file under the lane's sources/")
    path = case / "research" / "swarm" / str(lead["topic_id"]) / ref
    if path.is_symlink() or not path.is_file():
        raise MethodRefused(f"{ref} is missing")
    if hashlib.sha256(path.read_bytes()).hexdigest() != lead.get("content_sha256"):
        raise MethodRefused(f"{ref}: sha256 does not match the lead")
    return path


def _clue(ref: str, entry: Mapping[str, Any], lead: Mapping[str, Any], signals: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = sorted({str(s["candidate_id"]) for s in signals})
    shown = ", ".join(candidates[:MAX_CANDIDATES_SHOWN]) + (" …" if len(candidates) > MAX_CANDIDATES_SHOWN else "")
    key = json.dumps([ref, lead["lead_id"], candidates], ensure_ascii=False)
    caveat = "; ".join(entry.get("false_positives") or []) or "none declared"
    return {
        "clue_id": "method-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12],
        "type": CLUE_TYPE,
        "source": ref,
        "lead_ids": [lead["lead_id"]],
        "direction_ids": [lead["direction_id"]],
        "summary": f"{entry.get('title') or entry['id']}: {len(candidates)} flagged in the table from "
                   f"{lead['lead_id']} ({shown}).",
        "warning": f"A method signal is a lead to check, never a finding. Known false positives: {caveat}.",
    }


def method_clues(case: Path, leads_doc: Mapping[str, Any], methodology: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Clues from the approved methods on the lanes' tables, and the merge report lines.

    Never refuses the merge: a method that cannot run is reported and raises no clue."""
    approved = plan_methods(methodology)
    if not approved:
        return [], []
    try:
        home = anomaly_home()
        catalogue = load_catalogue(home)
        runner_python(home)
    except AnomalyUnavailable as exc:
        return [], [f"methods not run: {exc}"]
    available = runnable(catalogue)
    report: list[str] = []
    batches: dict[tuple[str, str, str], list[tuple[Mapping[str, Any], Mapping[str, str]]]] = {}
    leads: dict[tuple[str, str], Mapping[str, Any]] = {}
    for topic_id in sorted(approved):
        folder = case / "research" / "swarm" / topic_id
        try:
            inputs = _read_inputs(folder)
        except MethodRefused as exc:
            report.append(f"methods {topic_id}: {exc}")
            continue
        for index, item in enumerate(inputs):
            where = f"methods {topic_id} input {index}"
            problem = _valid_input(item)
            if problem:
                report.append(f"{where}: {problem}")
                continue
            ref = item["method"]
            if ref not in approved[topic_id]:
                report.append(f"{where}: {ref} is not approved for this topic")
                continue
            if ref not in available:
                report.append(f"{where}: {ref} is not a single-table method in the pinned catalogue")
                continue
            lead = _table_lead(leads_doc, topic_id, item["lead_id"])
            if lead is None:
                report.append(f"{where}: lead {item['lead_id']} is not among the merged leads")
                continue
            key = (str(lead["topic_id"]), str(lead["lead_id"]), item["format"])
            if any(method_ref(entry) == ref for entry, _f in batches.get(key, [])):
                report.append(f"{where}: {ref} already runs on this table")
                continue
            batches.setdefault(key, []).append((available[ref], item["fields"]))
            leads[key[:2]] = lead
    clues: list[dict[str, Any]] = []
    for (topic_id, lead_id, fmt), requests in sorted(batches.items()):
        lead = leads[(topic_id, lead_id)]
        try:
            signals = run_table(_table_file(case, lead), fmt, requests, catalogue, home)
        except (MethodRefused, AnomalyUnavailable, OSError) as exc:
            refs = ", ".join(method_ref(entry) for entry, _f in requests)
            report.append(f"methods on {lead_id} ({refs}): {exc}")
            continue
        for ref, found in sorted(signals.items()):
            report.append(f"methods on {lead_id}: {ref} gave {len(found)} signal(s)")
            if found:
                clues.append(_clue(ref, available[ref], lead, found))
    return clues, report


# ------------------------------------------------------------------ CLI
def main(argv: Iterable[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["state"]:
        print(json.dumps(state(), indent=2))
        return 0
    if args == ["methods"]:
        print(json.dumps({**state(), "methods": methods()}, indent=2, ensure_ascii=False))
        return 0
    print("usage: adapter.py state | methods", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
