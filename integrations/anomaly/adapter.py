#!/usr/bin/env python3
"""Anomaly as context and a runner: which data sources and detectors fit a
case, and the chosen detectors run on tables the investigator fetched.

    scripts/spotlight-python integrations/anomaly/adapter.py state
    scripts/spotlight-python integrations/anomaly/adapter.py context [--domain D | --source ID]
    scripts/spotlight-python integrations/anomaly/adapter.py run CASE_DIR < request.json

Anomaly (github.com/buriedsignals/Anomaly) stays a separate install, named by
``ANOMALY_HOME`` (its checkout, with its own ``.venv``), which Engine installs
and updates; Spotlight never changes it. Spotlight uses whatever catalogue is
installed, but only when ``detectors/catalogue.json`` has a supported
``schema_version`` and a ``catalogue_hash`` that matches the file's own
content; anything else hides the feature. Methodology approval records the
implementation hash of every detector the plan allows, and a run refuses a
detector whose hash has changed since.

``context`` is Anomaly's read-only lookup (``python -m anomaly.context``): each
domain (a type of data) with its sources and their SKILL.md, and the detectors
that read it; core detectors that run on any table are listed under
``any_table``. At Methodology the member opts in, and the methodology's
``anomaly`` section records the domains and sources judged useful.

``run`` is the Investigation step. A method is ``method:<detector id>@<version>``.
A core detector takes one ``table`` and ``fields`` (its field roles, such as id
or amount, to table columns); a domain detector takes ``tables`` (each contract
table it reads, to a file) and optional ``columns`` (per table, contract column
to file column). Every file is a case file under research/ or evidence/. The
runner gets read-only copies in a private temporary folder, an environment
without keys, and the request pinned to the catalogue's version and
implementation hash. A detector whose implementation hash differs from the one
recorded at Methodology approval is refused. Every output line is checked against the catalogue's
signal schema and the method's expected output, then the run is appended to
``data/anomaly-signals.json``. A signal is a lead to check, never a finding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

_CHECKOUT = Path(__file__).resolve().parents[2]
for _path in (_CHECKOUT, _CHECKOUT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from integrations._credentials import credential  # noqa: E402
from spotlight_orchestration.case_writer import atomic_write_files  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.storage import transaction  # noqa: E402

SCHEMA_VERSIONS = (1,)  # the catalogue schema versions this adapter reads
HOME_ENV = "ANOMALY_HOME"
METHOD_REF = re.compile(r"^method:(?P<id>[a-z0-9][a-z0-9._-]*)@(?P<version>[0-9A-Za-z][0-9A-Za-z.+-]*)$")
FORMATS = {".csv", ".jsonl", ".parquet"}
TABLE_ROOTS = ("research", "evidence")
SIGNALS_FILE = "data/anomaly-signals.json"
# The plan's name for the core detectors, which run on any table: tables may
# come from any Anomaly source, or from outside Anomaly.
ANY_TABLE = "any_table"
RUN_TIMEOUT_SECONDS = 120
CONTEXT_TIMEOUT_SECONDS = 60


class AnomalyUnavailable(Exception):
    """No Anomaly install, or one whose catalogue cannot be trusted."""


class MethodRefused(Exception):
    """A run request that cannot run, or output that is not what the method declares."""


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


def load_catalogue(home: Path | None = None) -> dict[str, Any]:
    """The installed catalogue, only when its schema is supported and its hash
    matches its own content."""
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
    if catalogue.get("schema_version") not in SCHEMA_VERSIONS:
        raise AnomalyUnavailable(f"catalogue schema {catalogue.get('schema_version')!r} is not one this Spotlight "
                                 f"reads ({', '.join(map(str, SCHEMA_VERSIONS))})")
    body = {key: value for key, value in catalogue.items() if key != "catalogue_hash"}
    if not isinstance(catalogue.get("catalogue_hash"), str) or _canonical_hash(body) != catalogue["catalogue_hash"]:
        raise AnomalyUnavailable("the catalogue's content does not match its catalogue_hash")
    return catalogue


def state() -> dict[str, Any]:
    """Whether the feature is shown, and why not."""
    try:
        home = anomaly_home()
        catalogue = load_catalogue(home)
        runner_python(home)
    except AnomalyUnavailable as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True, "reason": None, "catalogue_hash": catalogue["catalogue_hash"]}


def runnable(catalogue: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Every detector, by method ref: core ones read one table, domain ones named tables."""
    return {method_ref(entry): entry for entry in catalogue.get("detectors") or [] if isinstance(entry, Mapping)}


def is_core(entry: Mapping[str, Any]) -> bool:
    return entry.get("required_tables") == ["*"]


def _runner_env(scratch: Path) -> dict[str, str]:
    """No keys, tokens, proxies or Python hooks reach Anomaly."""
    env = {"PATH": os.defpath, "HOME": str(scratch), "TMPDIR": str(scratch), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONUTF8": "1"}
    for name in ("SYSTEMROOT", "WINDIR"):  # Windows cannot start Python without them
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


# ------------------------------------------------------------------ context
def context(domain: str | None = None, source: str | None = None) -> dict[str, Any]:
    """Anomaly's own lookup, run from the installed checkout with no keys."""
    home = anomaly_home()
    load_catalogue(home)
    python = runner_python(home)
    args = ["--domain", domain] if domain else ["--source", source] if source else []
    with tempfile.TemporaryDirectory(prefix="spotlight-anomaly-") as scratch_name:
        try:
            proc = subprocess.run([str(python), "-I", "-m", "anomaly.context", *args], cwd=home,
                                  env=_runner_env(Path(scratch_name)), capture_output=True, text=True,
                                  encoding="utf-8", timeout=CONTEXT_TIMEOUT_SECONDS, check=False,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise AnomalyUnavailable(f"the lookup did not finish within {CONTEXT_TIMEOUT_SECONDS} s") from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise MethodRefused(detail[-1][:300] if detail else f"the lookup failed ({proc.returncode})")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AnomalyUnavailable("the lookup did not print JSON") from None


def plan_domains(methodology: Mapping[str, Any]) -> list[dict[str, Any]]:
    section = methodology.get("anomaly")
    domains = section.get("domains") if isinstance(section, Mapping) else None
    return [entry for entry in domains if isinstance(entry, dict)] if isinstance(domains, list) else []


def plan_problems(methodology: Mapping[str, Any]) -> list[str]:
    """Methodology validation: every domain and source the plan names is one
    Anomaly lists. The schema owns the section's shape."""
    named = plan_domains(methodology)
    if not named:
        return []
    try:
        known = {entry["domain"]: {source["id"] for source in entry.get("sources") or []}
                 for entry in context().get("domains") or []}
    except (AnomalyUnavailable, MethodRefused) as exc:
        return [f"anomaly names domains, but the Anomaly lookup is unavailable ({exc})"]
    known[ANY_TABLE] = set().union(*known.values())
    problems = []
    for index, entry in enumerate(named):
        domain = entry.get("domain")
        if domain not in known:
            problems.append(f"anomaly.domains[{index}]: {domain!r} is not an Anomaly domain")
            continue
        problems += [f"anomaly.domains[{index}]: {source!r} is not an Anomaly source for {domain}"
                     for source in entry.get("sources") or [] if source not in known[domain]]
    return problems


def approved_methods(methodology: Mapping[str, Any], home: Path | None = None) -> dict[str, str]:
    """What Methodology approval records: the implementation hash of every
    detector the plan allows (the core ones and those of the domains it
    names), by method ref. Empty without a plan or without a usable install."""
    domains = {entry.get("domain") for entry in plan_domains(methodology)}
    if not domains:
        return {}
    try:
        catalogue = load_catalogue(anomaly_home() if home is None else home)
    except AnomalyUnavailable:
        return {}
    return {ref: str(entry.get("implementation_hash")) for ref, entry in runnable(catalogue).items()
            if is_core(entry) or entry.get("data_type") in domains}


def _approved_hashes(case: Path) -> dict[str, str]:
    approval = (_read_json(case / "data" / "orchestration.json").get("approvals") or {}).get("methodology")
    recorded = approval.get("anomaly_methods") if isinstance(approval, dict) else None
    return recorded if isinstance(recorded, dict) else {}


def view(methodology: Mapping[str, Any]) -> dict[str, Any] | None:
    """The Methodology page's data: the domains and sources the plan names,
    each with the detectors that read that type of data. None without a plan."""
    named = plan_domains(methodology)
    if not named:
        return None
    try:
        lookup = context()
    except (AnomalyUnavailable, MethodRefused) as exc:
        return {"available": False, "reason": str(exc), "domains": named}
    by_domain = {entry["domain"]: entry for entry in lookup.get("domains") or []}
    shown = []
    for entry in named:
        known = by_domain.get(entry.get("domain"), {})
        if entry.get("domain") == ANY_TABLE:
            known = {"sources": [source for domain in by_domain.values() for source in domain.get("sources") or []],
                     "detectors": lookup.get("any_table") or []}
        sources = {source["id"]: source for source in known.get("sources") or []}
        shown.append({
            "domain": entry.get("domain"),
            "why": entry.get("why"),
            "sources": [{key: sources.get(source_id, {}).get(key) for key in ("id", "title", "status", "auth", "cost", "license")}
                        | {"id": source_id} for source_id in entry.get("sources") or []],
            "detectors": [_describe(detector) for detector in known.get("detectors") or []],
        })
    return {"available": True, "domains": shown,
            "any_table": [_describe(detector) for detector in lookup.get("any_table") or []]}


def _describe(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "method": method_ref(entry),
        "title": entry.get("title"),
        "assumptions": list(entry.get("assumptions") or []),
        "false_positives": list(entry.get("false_positives") or []),
    }


# ------------------------------------------------------------------ running
def check_signal(signal: Any, entry: Mapping[str, Any], input_hash: str, input_hashes: Mapping[str, str] | None,
                 schema: Mapping[str, Any]) -> list[str]:
    """Problems with one output line, against the catalogue's signal schema and the method's declaration.
    Provenance has field_map for a core detector, and column_map and input_hashes for a domain one."""
    if not isinstance(signal, dict):
        return ["an output line is not an object"]
    problems = []
    if set(signal) != set(schema):
        problems.append(f"fields {sorted(set(signal) ^ set(schema))} differ from the signal schema")
    provenance = signal.get("provenance")
    expected_keys = set(schema.get("provenance") or {}) if isinstance(schema.get("provenance"), Mapping) else set()
    domain_only = {"column_map", "input_hashes"}
    expected_keys = ((expected_keys - {"field_map"}) | domain_only) if input_hashes is not None else expected_keys - domain_only
    if not isinstance(provenance, dict) or set(provenance) != expected_keys:
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
    if input_hashes is not None and provenance.get("input_hashes") != dict(input_hashes):
        problems.append("provenance.input_hashes are not the tables that were supplied")
    if provenance.get("approval") != "caller-recorded":
        problems.append("provenance.approval is not caller-recorded")
    values = signal.get("values")
    missing = [column for column in entry.get("expected_output") or []
               if not isinstance(values, dict) or column not in values]
    if missing:
        problems.append(f"values lack the method's expected output {missing}")
    return problems


def _case_table(case: Path, relative: Any) -> Path:
    """A case file under research/ or evidence/ in a format the runner reads."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise MethodRefused("a table is a case-relative path")
    parts = PurePosixPath(relative).parts
    if PurePosixPath(relative).is_absolute() or ".." in parts or parts[0] not in TABLE_ROOTS:
        raise MethodRefused(f"{relative}: a table is a case file under research/ or evidence/")
    if PurePosixPath(relative).suffix.lower() not in FORMATS:
        raise MethodRefused(f"{relative}: a table is a .csv, .jsonl or .parquet file")
    path = case / relative
    if any((case / Path(*parts[:end])).is_symlink() for end in range(1, len(parts) + 1)) or not path.is_file():
        raise MethodRefused(f"{relative} is not a regular case file")
    return path


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _string_map(value: Any, what: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value or not all(
            isinstance(key, str) and key and isinstance(item, str) and item for key, item in value.items()):
        raise MethodRefused(f"{what} must map names to non-empty strings")
    return dict(value)


def _parse_request(case: Path, request: Any, catalogue: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Path], dict[str, Any]]:
    """The detector entry, the tables by name ("*" for a core detector's one
    table) and the runner request, or MethodRefused."""
    if not isinstance(request, dict):
        raise MethodRefused("the request is a JSON object")
    ref = request.get("method")
    entry = runnable(catalogue).get(ref) if isinstance(ref, str) else None
    if entry is None:
        raise MethodRefused(f"{ref!r} is not a method in the installed Anomaly catalogue")
    pinned = {"id": entry["id"], "version": entry["version"], "implementation_hash": entry["implementation_hash"]}
    if is_core(entry):
        if set(request) != {"method", "table", "fields"}:
            raise MethodRefused(f"{ref} reads one table: give exactly method, table and fields")
        fields = _string_map(request["fields"], "fields")
        required = [field.get("name") for field in entry.get("required_fields") or []]
        unmapped = [name for name in required if name not in fields]
        if unmapped:
            raise MethodRefused(f"{ref} needs a column for each of {required}; missing {unmapped}")
        return entry, {"*": _case_table(case, request["table"])}, {**pinned, "fields": fields}
    if not set(request) <= {"method", "tables", "columns"} or "tables" not in request:
        raise MethodRefused(f"{ref} reads named tables: give method, tables and optionally columns")
    names = _string_map(request["tables"], "tables")
    required = list(entry.get("required_tables") or [])
    if sorted(names) != sorted(required):
        raise MethodRefused(f"{ref} reads exactly the tables {required}")
    runner_request: dict[str, Any] = dict(pinned)
    if "columns" in request:
        columns = request["columns"]
        if not isinstance(columns, dict) or set(columns) - set(required):
            raise MethodRefused(f"columns may only name {ref}'s tables")
        runner_request["columns"] = {name: _string_map(mapping, f"columns.{name}") for name, mapping in columns.items()}
    return entry, {name: _case_table(case, path) for name, path in names.items()}, runner_request


def run_method(case: Path, request: Any, home: Path | None = None) -> dict[str, Any]:
    """Run one approved method on the investigator's tables and append the run
    to data/anomaly-signals.json; return what the investigator is told."""
    home = anomaly_home() if home is None else home
    catalogue = load_catalogue(home)
    python = runner_python(home)
    methodology = _read_json(case / "data" / "methodology.json")
    domains = {entry.get("domain") for entry in plan_domains(methodology)}
    if not domains:
        raise MethodRefused("the approved methodology has no anomaly section: the member did not opt in to Anomaly")
    entry, tables, runner_request = _parse_request(case, request, catalogue)
    if not is_core(entry) and entry.get("data_type") not in domains:
        raise MethodRefused(f"{method_ref(entry)} reads {entry.get('data_type')} data, which the approved methodology does not name")
    recorded = _approved_hashes(case)
    approved = recorded.get(method_ref(entry))
    if approved != entry.get("implementation_hash"):
        if not recorded:
            raise MethodRefused("Anomaly was not available when the methodology was approved; re-approve")
        raise MethodRefused(f"{method_ref(entry)}: detector changed since approval; re-approve" if approved
                            else f"{method_ref(entry)} was not in the Anomaly catalogue when the methodology was approved; re-approve")
    data = {name: path.read_bytes() for name, path in tables.items()}
    hashes = {name: _sha256(content) for name, content in data.items()}
    named = "*" not in tables
    input_hash = (_sha256(json.dumps(hashes, sort_keys=True).encode("utf-8")) if named else hashes["*"])
    with tempfile.TemporaryDirectory(prefix="spotlight-anomaly-") as scratch_name:
        scratch = Path(scratch_name)
        os.chmod(scratch, 0o700)
        copies = {}
        for name, path in tables.items():
            copy = scratch / f"{'table' if name == '*' else name}{path.suffix.lower()}"
            copy.write_bytes(data[name])
            os.chmod(copy, stat.S_IRUSR)
            copies[name] = copy
        request_file = scratch / "request.json"
        request_file.write_text(json.dumps([runner_request]), encoding="utf-8")
        os.chmod(request_file, stat.S_IRUSR)
        source = ([arg for name, copy in sorted(copies.items()) for arg in ("--tables", f"{name}={copy}")]
                  if named else ["--table", str(copies["*"])])
        try:
            proc = subprocess.run(
                [str(python), "-I", "-m", "anomaly.oneshot", *source, "--request", str(request_file), "--approved"],
                cwd=scratch, env=_runner_env(scratch), capture_output=True, text=True, encoding="utf-8",
                timeout=RUN_TIMEOUT_SECONDS, check=False, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise MethodRefused(f"the runner did not finish within {RUN_TIMEOUT_SECONDS} s") from None
        if any(_sha256(copies[name].read_bytes()) != hashes[name] for name in copies):
            raise MethodRefused("the runner changed its read-only table copy")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise MethodRefused(f"the runner refused ({proc.returncode}): {detail[-1][:300] if detail else 'no message'}")
    schema = catalogue.get("signal_schema") or {}
    signals = []
    for number, line in enumerate(proc.stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            signal = json.loads(line)
        except json.JSONDecodeError:
            raise MethodRefused(f"output line {number} is not JSON") from None
        problems = check_signal(signal, entry, input_hash, hashes if named else None, schema)
        if problems:
            raise MethodRefused(f"{method_ref(entry)} output line {number}: {problems[0]}")
        signals.append(signal)
    record = {
        "method": method_ref(entry),
        "ran_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "tables": {name: {"path": str(request.get("table") if name == "*" else request["tables"][name]), "sha256": hashes[name]}
                   for name in tables},
        "map": runner_request.get("fields") or runner_request.get("columns") or {},
        "catalogue_hash": catalogue["catalogue_hash"],
        "implementation_hash": entry.get("implementation_hash"),
        "assumptions": list(entry.get("assumptions") or []),
        "false_positives": list(entry.get("false_positives") or []),
        "signals": signals,
    }
    _append_run(case, record)
    candidates = sorted({str(signal.get("candidate_id")) for signal in signals})
    return {
        "ok": True,
        "method": record["method"],
        "signals": len(signals),
        "candidates": candidates[:20],
        "assumptions": record["assumptions"],
        "false_positives": record["false_positives"],
        "note": f"Recorded in {SIGNALS_FILE}. Each signal is a lead to check against the source, never a finding.",
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _append_run(case: Path, record: Mapping[str, Any]) -> None:
    with transaction(case) as descriptor:
        document = _read_json(case / SIGNALS_FILE) or {"schema_version": "1.0", "runs": []}
        runs = document.get("runs") if isinstance(document.get("runs"), list) else []
        document = {"schema_version": "1.0", "runs": [*runs, dict(record)]}
        payload = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        atomic_write_files(case, {SIGNALS_FILE: payload}, data_descriptor=descriptor)


def signal_runs(case: Path) -> list[dict[str, Any]]:
    runs = _read_json(case / SIGNALS_FILE).get("runs")
    return [run for run in runs if isinstance(run, dict)] if isinstance(runs, list) else []


# ------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("state")
    lookup = sub.add_parser("context")
    scope = lookup.add_mutually_exclusive_group()
    scope.add_argument("--domain")
    scope.add_argument("--source")
    runner = sub.add_parser("run")
    runner.add_argument("case_dir", type=Path)
    args = parser.parse_args(argv)
    if args.command == "state":
        print(json.dumps(state(), indent=2))
        return 0
    try:
        if args.command == "context":
            # A hidden feature is an answer, not a refusal: available false and why.
            current = state()
            result: Any = {**current, **(context(args.domain, args.source) if current["available"]
                                         else {"domains": [], "any_table": []})}
        else:
            result = run_method(args.case_dir.resolve(), json.loads(sys.stdin.read() or "null"))
    except (AnomalyUnavailable, MethodRefused, OrchestrationError, json.JSONDecodeError) as exc:
        print(f"anomaly: refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
