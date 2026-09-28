"""Shared, offline helpers for decision-model signal files.

Used by scripts/decision-signals.py (the only networked step), the report
renderer, the report finalizer and the ingest eligibility helper. Everything
here is deterministic and makes no network request.

Two case files:

* ``data/decision-signals.json`` — Gate 1 grounding and report-prose signals.
  The report renderer hashes it as an input.
* ``data/decision-signals-ingest.json`` — ingest signals. It is kept separate so
  that ingest checks after report finalization do not invalidate the report.

A signal applies only while its input fingerprint still matches the current
case files; otherwise it is stale and ignored. A reviewer override applies only
to the exact fingerprint the reviewer saw.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
import unicodedata
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

from spotlight_orchestration.case_writer import atomic_write_files
from spotlight_orchestration.contract import OrchestrationError
from spotlight_orchestration.storage import read_data_bytes, transaction

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW
ANCHORED_READS = os.open in os.supports_dir_fd and bool(_NOFOLLOW)
PINNED_CHILDREN = ("data", "research")
_PINNED: dict[str, dict[str, int]] = {}


@contextmanager
def pinned_case(case: Path) -> Iterator[Path]:
    """Pin the case root and its data/ and research/ directories before anything is read.

    Networked callers enter this before reading consent. Consent, every request
    input and the signal write then come from these same directory inodes, so
    renaming or symlink-swapping the case, its data/ or its research/ during the
    run cannot bring another case's content under this case's consent. (Moving
    individual files into the pinned directories is equivalent to adding them to
    the case.) The yielded Path must be passed unchanged to readers.
    """
    if not ANCHORED_READS:
        raise SignalsError("descriptor-anchored reads are unavailable on this platform")
    resolved = case.resolve()
    descriptors: dict[str, int] = {}
    try:
        descriptors[""] = os.open(resolved, _DIRECTORY)
        for child in PINNED_CHILDREN:
            try:
                descriptors[child] = os.open(child, _DIRECTORY, dir_fd=descriptors[""])
            except FileNotFoundError:
                continue
    except OSError as exc:
        for descriptor in descriptors.values():
            os.close(descriptor)
        raise SignalsError(f"case directory is not safely accessible: {exc}") from exc
    _PINNED[str(resolved)] = descriptors
    try:
        yield resolved
    finally:
        _PINNED.pop(str(resolved), None)
        for descriptor in descriptors.values():
            os.close(descriptor)


def _open_parent(case: Path, parts: tuple[str, ...]) -> int:
    """A descriptor for the directory holding the target, walked without following symlinks."""
    pinned = _PINNED.get(str(case))
    if pinned is not None and parts and parts[0] in pinned:
        descriptor, parts = os.dup(pinned[parts[0]]), parts[1:]
    elif pinned is not None:
        descriptor = os.dup(pinned[""])
    else:
        descriptor = os.open(case.resolve(), _DIRECTORY)
    try:
        for part in parts:
            child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor

SIGNALS_NAME = "decision-signals.json"
INGEST_SIGNALS_NAME = "decision-signals-ingest.json"
SIGNALS_PATH = f"data/{SIGNALS_NAME}"
INGEST_SIGNALS_PATH = f"data/{INGEST_SIGNALS_NAME}"
SCHEMA_VERSION = "1.0"
MODES = ("shadow", "advisory", "enforce")
CAP_ORDER = {"low": 1, "medium": 2, "high": 3}
EXCERPT_WIDTH = 700
MAX_SOURCES = 2
SCRIPT_DIR = Path(__file__).resolve().parent

VERDICT_FRAME = {
    "verified": "Established: {c}",
    "partially_verified": "Only partly established (part of this could not be confirmed): {c}",
    "unverified": "Not established (this could not be verified): {c}",
    "disputed": "Disputed (credible sources conflict on this): {c}",
    "false": "Established as false (this claim is not true): {c}",
    "mischaracterized": "Established as mischaracterized (this claim misrepresents the record): {c}",
}
PROSE_FIELDS = ("headline", "summary", "why_it_matters")


class SignalsError(ValueError):
    """A decision-signals file exists but cannot be trusted."""


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " … ".join(text(item) for item in value if text(item))
    if isinstance(value, dict):
        return text(value.get("quote") or value.get("text") or value.get("excerpt") or "")
    return str(value).strip()


def read_case_file(case: Path, relative: str) -> bytes | None:
    """Read a case-relative file through directory descriptors, never following a symlink.

    Every path component is opened with O_NOFOLLOW relative to its verified
    parent descriptor and the opened file is checked with fstat, so a
    concurrent swap cannot redirect the read outside the case. Returns None
    when the file is absent; raises SignalsError for escaping, symlinked or
    non-regular paths.
    """
    posix = PurePosixPath(relative)
    parts = posix.parts
    if posix.is_absolute() or not parts or any(part in ("", ".", "..") for part in parts):
        raise SignalsError(f"invalid case-relative path: {relative}")
    if not ANCHORED_READS:
        # Offline consumers only (networked callers refuse to run without anchored reads).
        path = case.resolve().joinpath(*parts)
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise SignalsError(f"{relative} must be a regular, non-symlinked case file")
        return path.read_bytes()
    try:
        directory = _open_parent(case, tuple(parts[:-1]))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SignalsError(f"{relative} is not safely reachable inside the case: {exc}") from exc
    try:
        try:
            descriptor = os.open(parts[-1], os.O_RDONLY | _NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SignalsError(f"{relative} is not a safely readable case file: {exc}") from exc
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise SignalsError(f"{relative} must be a regular file")
            return stream.read()
    finally:
        os.close(directory)


def read_case_json(case: Path, name: str, expect: type | None = dict, required: bool = False) -> Any:
    """Read data/<name> without following symlinks or leaving the case.

    None when absent (SignalsError instead when ``required``). A present file
    must parse to ``expect``; JSON null or another type is invalid, never absent.
    """
    content = read_case_file(case, f"data/{name}")
    if content is None:
        if required:
            raise SignalsError(f"data/{name} does not exist")
        return None
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SignalsError(f"data/{name} is not valid JSON: {exc}") from exc
    if expect is not None and not isinstance(value, expect):
        raise SignalsError(f"data/{name} must contain a JSON {expect.__name__}")
    return value


def case_findings(case: Path) -> list[dict[str, Any]]:
    doc = read_case_json(case, "findings.json") or {}
    return [f for f in doc.get("findings") or [] if isinstance(f, dict)]


def valid_opt_in(value: Any) -> bool:
    """A consent record names who agreed and when; anything else is not consent."""
    return (isinstance(value, dict) and isinstance(value.get("by"), str) and value["by"].strip() != ""
            and isinstance(value.get("at"), str) and value["at"].strip() != "")


def frame_finding(claim: str, verdict: str) -> str:
    """Present a finding as what the fact-check established (state shaping for prose checks)."""
    return VERDICT_FRAME.get(verdict, "{c}").format(c=claim)


def render_module():
    spec = importlib.util.spec_from_file_location("spotlight_render_report", SCRIPT_DIR / "render-report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ------------------------------------------------------------------ excerpts
def _clean(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace("\\n", " ")
    value = re.sub(r"\]\((?:https?://|/)[^)]*\)", "]", value)
    value = re.sub(r"!\[[^\]]*\]", " ", value)
    value = re.sub(r"[*#|\[\]_`>]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def locate_excerpt(document: str, quote: str, width: int = EXCERPT_WIDTH) -> str | None:
    """Return a cleaned window of ``document`` around the longest matching run of ``quote``."""
    cleaned = _clean(document)
    lowered = cleaned.lower()
    words = _clean(quote).lower().replace("…", " ").replace("...", " ").split()
    for size in (10, 8, 6, 5, 4, 3):
        for start in range(0, max(1, len(words) - size + 1)):
            fragment = " ".join(words[start:start + size])
            if len(fragment) < 12:
                continue
            index = lowered.find(fragment)
            if index >= 0:
                return cleaned[max(0, index - width): index + len(fragment) + width]
    return None


def document_heading(document: str) -> str:
    match = re.search(r"^#+\s*(.+)$", document, re.M) or re.search(r"^title:\s*(.+)$", document, re.M | re.I)
    return _clean(match.group(1))[:200] if match else ""


def _contained(case: Path, path: Path) -> Path | None:
    """Resolve ``path`` and return it only if it is a regular file inside the case."""
    try:
        resolved = path.resolve()
        resolved.relative_to(case.resolve())
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _directory_contained(case: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(case.resolve())
    except (OSError, ValueError):
        return False
    return True


def case_path(case: Path, candidate: Any) -> Path | None:
    """Resolve a finding's local_file inside the case directory only."""
    raw = text(candidate)
    if not raw:
        return None
    path = Path(raw)
    return _contained(case, path if path.is_absolute() else case / path)


def _safe_text(case: Path, path: Path) -> str | None:
    """Read a stored source through the anchored reader; skip anything unsafe or unreadable."""
    try:
        relative = path.relative_to(case) if not path.is_absolute() else path.absolute().relative_to(case.resolve())
    except ValueError:
        try:
            relative = path.resolve().relative_to(case.resolve())
        except (OSError, ValueError):
            return None
    try:
        content = read_case_file(case, relative.as_posix())
    except SignalsError:
        return None
    return None if content is None else content.decode("utf-8", errors="ignore")


def case_input_name(case: Path, value: Any) -> str:
    """A CLI-supplied ingest input must be a plain file directly inside {CASE_DIR}/data/."""
    raw = Path(str(value))
    candidate = raw if raw.is_absolute() else Path.cwd() / raw
    data_dir = case.resolve() / "data"
    # The parent directory must be the case's data directory; the file itself is then
    # opened by name through the anchored reader, which refuses a symlinked file.
    if os.path.realpath(candidate.parent) != os.path.realpath(data_dir) or candidate.name in ("", ".", ".."):
        raise SignalsError(f"{value} must be a file directly inside {data_dir}")
    return candidate.name


def grounding_sources(case: Path, finding: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    """Locate the finding's quoted evidence in stored sources inside the case.

    Returns (sources, location) where location is one of listed_source,
    other_case_file, not_found or no_evidence. Symlinks that lead outside the
    case are never read.
    """
    quote = text(finding.get("evidence"))
    if not quote:
        return [], "no_evidence"
    located: list[dict[str, str]] = []
    for source in finding.get("sources", []):
        if not isinstance(source, dict):
            continue
        path = case_path(case, source.get("local_file"))
        document = _safe_text(case, path) if path is not None else None
        if document is None:
            continue
        excerpt = locate_excerpt(document, quote)
        if excerpt:
            located.append({"url": text(source.get("url")), "document_heading": document_heading(document), "excerpt": excerpt})
    if located:
        return located[:MAX_SOURCES], "listed_source"
    research = case / "research"
    if research.is_dir() and _directory_contained(case, research):
        for candidate in sorted(research.rglob("*.md")):
            path = _contained(case, candidate)
            document = _safe_text(case, candidate) if path is not None else None
            if document is None:
                continue
            excerpt = locate_excerpt(document, quote)
            if excerpt:
                return [{"url": "", "document_heading": document_heading(document) or path.name, "excerpt": excerpt}], "other_case_file"
    return [], "not_found"


# ------------------------------------------------------------------ fingerprints
def grounding_state(case: Path, finding: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]], str]:
    """Everything the grounding check depends on: the finding and the located source excerpts."""
    sources, location = grounding_sources(case, finding)
    state = {
        "claim": text(finding.get("claim")),
        "evidence": text(finding.get("evidence")),
        "declared_sources": [
            {"url": text(s.get("url")), "local_file": text(s.get("local_file"))}
            for s in finding.get("sources", []) if isinstance(s, dict)
        ],
        "location": location,
        "located": [
            {"url": s["url"], "document_heading": s["document_heading"],
             "excerpt_sha256": hashlib.sha256(s["excerpt"].encode("utf-8")).hexdigest()}
            for s in sources
        ],
    }
    return state, sources, location


def grounding_fingerprint(case: Path, finding: dict[str, Any]) -> str:
    return canonical_sha256(grounding_state(case, finding)[0])


# ------------------------------------------------------------------ signals files
def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SignalsError(message)


def validate_signals(doc: Any) -> dict[str, Any]:
    """Structural validation of everything the offline consumers read."""
    _require(isinstance(doc, dict), "decision signals must be a JSON object")
    _require(doc.get("schema_version") == SCHEMA_VERSION, "unsupported decision-signals schema_version")
    _require(isinstance(doc.get("phases"), dict), "decision signals need a phases object")
    _require(isinstance(doc.get("overrides", []), list), "decision-signals overrides must be a list")
    _require(doc.get("opt_in") is None or valid_opt_in(doc.get("opt_in")), "opt_in must be null or name who agreed and when")
    for override in doc.get("overrides", []):
        _require(isinstance(override, dict), "each override must be an object")
    for override in doc.get("overrides", []):
        _require(isinstance(override.get("target"), str) and isinstance(override.get("input_sha256"), str),
                 "each override needs a target and the input_sha256 it reviewed")
    required = {"gate1": ("findings",), "report": ("items",), "ingest": ()}
    optional = {"gate1": (), "report": (), "ingest": ("propositions", "memberships", "entities", "matches")}
    for name, phase in doc["phases"].items():
        _require(name in required and isinstance(phase, dict), f"invalid phase {name!r}")
        _require(phase.get("mode") in MODES, f"phase {name} has an invalid mode")
        for group in required[name] + optional[name]:
            if group not in phase:
                _require(group not in required[name], f"{name}.{group} is missing")
                continue
            entries = phase[group]
            _require(isinstance(entries, list), f"{name}.{group} must be a list")
            for entry in entries:
                _require(isinstance(entry, dict), f"{name}.{group} entries must be objects")
                _require(isinstance(entry.get("input_sha256"), str), f"{name}.{group} entry lacks input_sha256")
                _require(entry.get("status") in ("judged", "routed", "unavailable"), f"{name}.{group} entry has an invalid status")
                flags = entry.get("flags")
                _require(isinstance(flags, list) and all(isinstance(f, dict) and isinstance(f.get("reason"), str) for f in flags),
                         f"{name}.{group} flags must be objects with a reason")
                if name == "gate1":
                    _require(isinstance(entry.get("finding_id"), str), "gate1 entries need a finding_id")
                    _require(entry.get("cap", "none") in ("none", "medium", "low"), "gate1 cap must be none, medium or low")
                if name == "report":
                    _require(isinstance(entry.get("target"), str) and isinstance(entry.get("finding_ids"), list),
                             "report entries need a target and finding_ids")
                if name == "ingest":
                    _require(isinstance(entry.get("target"), str), "ingest entries need a target")
    return doc


def load_signals(case: Path, name: str = SIGNALS_NAME) -> dict[str, Any] | None:
    """None when absent; SignalsError when present but unreadable, malformed or a symlink."""
    doc = read_case_json(case, name)
    return None if doc is None else validate_signals(doc)


def update_signals(case: Path, name: str, mutate: Callable[[dict[str, Any] | None], dict[str, Any]]) -> dict[str, Any]:
    """Locked read-modify-write through descriptor-anchored, no-follow atomic publication.

    Under ``pinned_case`` the data directory is reached from the pinned root
    descriptor, so a swapped case pathname cannot redirect the write.
    """
    if str(case) in _PINNED:
        return _update_pinned(case, name, mutate)
    case = case.resolve()
    try:
        with transaction(case) as descriptor:
            content = read_data_bytes(case, name, descriptor)
            current = None
            if content is not None:
                try:
                    current = validate_signals(json.loads(content.decode("utf-8")))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise SignalsError(f"data/{name} is invalid; move it aside before rerunning: {exc}") from exc
            updated = validate_signals(mutate(current))
            payload = (json.dumps(updated, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            atomic_write_files(case, {f"data/{name}": payload}, data_descriptor=descriptor)
            return updated
    except OrchestrationError as exc:
        raise SignalsError(str(exc)) from exc


def _update_pinned(case: Path, name: str, mutate: Callable[[dict[str, Any] | None], dict[str, Any]]) -> dict[str, Any]:
    import fcntl

    try:
        descriptor = _open_parent(case, ("data",))
    except OSError as exc:
        raise SignalsError(f"case data directory is not safely accessible: {exc}") from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            content = read_data_bytes(case, name, descriptor)
        except OrchestrationError as exc:
            raise SignalsError(str(exc)) from exc
        current = None
        if content is not None:
            try:
                current = validate_signals(json.loads(content.decode("utf-8")))
            except (UnicodeDecodeError, ValueError) as exc:
                raise SignalsError(f"data/{name} is invalid; move it aside before rerunning: {exc}") from exc
        updated = validate_signals(mutate(current))
        payload = (json.dumps(updated, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            atomic_write_files(case, {f"data/{name}": payload}, data_descriptor=descriptor)
        except OrchestrationError as exc:
            raise SignalsError(str(exc)) from exc
        return updated
    finally:
        os.close(descriptor)  # closing releases the lock


# ------------------------------------------------------------------ overrides
def override_for(signals: dict[str, Any] | None, target: str, input_sha256: str) -> dict[str, Any] | None:
    """An override counts only for the exact reviewed input and with complete reviewer metadata."""
    for item in (signals or {}).get("overrides", []):
        if (isinstance(item, dict) and item.get("target") == target
                and item.get("input_sha256") == input_sha256
                and all(isinstance(item.get(k), str) and item[k].strip() for k in ("reviewer", "reason", "at"))):
            return item
    return None


# ------------------------------------------------------------------ gate1 consumers
def finding_signal(signals: dict[str, Any] | None, case: Path, finding: dict[str, Any]) -> dict[str, Any] | None:
    """The gate1 signal for a finding, marked stale when its inputs changed since it was computed."""
    if not signals:
        return None
    phase = (signals.get("phases") or {}).get("gate1") or {}
    for entry in phase.get("findings", []):
        if entry.get("finding_id") == text(finding.get("id")):
            result = dict(entry)
            result["current_input_sha256"] = grounding_fingerprint(case, finding)
            if entry.get("input_sha256") != result["current_input_sha256"]:
                result["status"] = "stale"
            result["mode"] = phase.get("mode", "advisory")
            return result
    return None


def enforced_cap(signal: dict[str, Any] | None, signals: dict[str, Any] | None) -> str | None:
    """Cap to apply to displayed confidence, or None. Only judged, fresh, enforced, non-overridden signals cap."""
    if not signal or signal.get("status") != "judged" or signal.get("mode") != "enforce":
        return None
    if override_for(signals, f"finding:{signal.get('finding_id')}", text(signal.get("input_sha256"))):
        return None
    cap = signal.get("cap")
    return cap if cap in CAP_ORDER else None


def effective_verdict(render: Any, case: Path, finding: dict[str, Any], checks: list[dict[str, Any]],
                      signals: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Fact-check verdict with any enforced decision cap applied (never raised)."""
    verdict = render.aggregate_verdict(finding, checks)
    signal = finding_signal(signals, case, finding)
    cap = enforced_cap(signal, signals)
    if cap and CAP_ORDER[cap] < CAP_ORDER.get(verdict["confidence"], 1):
        verdict = {**verdict, "confidence": cap, "decision_cap": cap}
    return verdict, signal


# ------------------------------------------------------------------ report prose
def report_items(case: Path, signals: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The single definition of what the report check judges, shared by producer and finalizer.

    Each item's fingerprint covers the sentence, its field, the findings it
    cites (identity, claim, verdict and the confidence the reader will see).
    """
    render = render_module()
    by_id = {text(f.get("id")): f for f in case_findings(case)}
    checks = render.canonical_checks(read_case_json(case, "fact-check.json") or {})
    verdicts = {fid: effective_verdict(render, case, f, checks, signals)[0] for fid, f in by_id.items()}
    draft = read_case_json(case, "report-draft.json") or {}
    targets: list[tuple[str, str, str, list[str]]] = [
        ("deck", "deck", text(draft.get("deck")), [text(i) for i in draft.get("framing_finding_ids", [])])]
    for treatment in draft.get("finding_treatments", []):
        if isinstance(treatment, dict):
            fid = text(treatment.get("finding_id"))
            targets += [(f"treatment:{fid}.{field}", field, text(treatment.get(field)), [fid]) for field in PROSE_FIELDS]
    for index, caveat in enumerate(draft.get("caveats", [])):
        if isinstance(caveat, dict):
            targets.append((f"caveat:{index}", "caveat", text(caveat.get("text")), [text(i) for i in caveat.get("finding_ids", [])]))
    items = []
    for target, field, sentence, fids in targets:
        fids = [fid for fid in fids if fid in by_id]
        if not sentence or not fids:
            continue
        statuses = sorted({verdicts[fid]["status"] for fid in fids})
        verdict = statuses[0] if len(statuses) == 1 else "mixed"
        framed = [frame_finding(text(by_id[fid].get("claim")), verdicts[fid]["status"]) for fid in fids]
        confidence = min((verdicts[fid]["confidence"] for fid in fids), key=lambda c: CAP_ORDER.get(c, 1))
        state = {"approved_finding": framed[0] if len(framed) == 1 else framed, "verdict": verdict,
                 "confidence": confidence, "field": field, "sentence": sentence}
        # The model sees the weakest confidence; the fingerprint keeps every finding's own values.
        detail = [{"id": fid, "claim": text(by_id[fid].get("claim")), "verdict": verdicts[fid]["status"],
                   "confidence": verdicts[fid]["confidence"]} for fid in fids]
        items.append({"target": target, "finding_ids": fids, "state": state,
                      "input_sha256": canonical_sha256({"state": state, "findings": detail})})
    return items


# ------------------------------------------------------------------ ingest (knowledge batch)
def batch_items(case: Path) -> list[dict[str, Any]]:
    """Proposition and claim-event checks for data/knowledge-batch.json, shared by producer and consumers.

    Records are resolved by exact (id, version). Every input that can change the
    derived result, including endpoint identities and versions, is part of
    ``state`` or ``rule_inputs`` and therefore of the fingerprint. A membership
    whose endpoints cannot be resolved is returned with ``unresolved: True``.
    """
    batch = read_case_json(case, "knowledge-batch.json")
    if not isinstance(batch, dict):
        return []
    by_id = {text(f.get("id")): f for f in case_findings(case)}

    def key(ref: Any) -> tuple[str, str]:
        ref = ref if isinstance(ref, dict) else {}
        return text(ref.get("id")), text(ref.get("version"))

    claims = {key(c): c for c in batch.get("claims", []) if isinstance(c, dict)}
    events = {key(e): e for e in batch.get("events", []) if isinstance(e, dict)}
    items: list[dict[str, Any]] = []

    def item(group: str, target: str, state: dict[str, Any], rule_inputs: dict[str, Any], unresolved: bool = False) -> None:
        items.append({"group": group, "target": target, "state": state, "rule_inputs": rule_inputs, "unresolved": unresolved,
                      "input_sha256": canonical_sha256({"state": state, "rule_inputs": rule_inputs})})

    for (cid, version), claim in claims.items():
        finding = by_id.get(text((claim.get("origin") or {}).get("finding_id")))
        state = {"finding_claim": text((finding or {}).get("claim")), "proposition": text(claim.get("proposition"))}
        item("propositions", f"claim:{cid}@{version}", state, {"claim_version": version}, unresolved=finding is None)
    for membership in batch.get("claim_event_memberships", []):
        if not isinstance(membership, dict):
            continue
        claim_ref, event_ref = key(membership.get("claim")), key(membership.get("event"))
        claim, event = claims.get(claim_ref), events.get(event_ref)
        target = f"membership:{text(membership.get('id'))}@{text(membership.get('version'))}"
        state = {"claim": text((claim or {}).get("proposition")), "event_label": text((event or {}).get("label")),
                 "event_core": (event or {}).get("core") or {}}
        rule_inputs = {"declared_relation": text(membership.get("relation")),
                       "claim_ref": list(claim_ref), "event_ref": list(event_ref)}
        item("memberships", target, state, rule_inputs, unresolved=claim is None or event is None)
    return items


def entity_items(entities: list[Any]) -> list[dict[str, Any]]:
    """Entity-type checks for an agent-provided list [{name, context, type}]."""
    items = []
    for entity in entities:
        if isinstance(entity, dict):
            state = {"entity_name": text(entity.get("name")), "context_sentence": text(entity.get("context"))}
            rule_inputs = {"declared_type": text(entity.get("type"))}
            items.append({"group": "entities", "target": f"entity:{state['entity_name']}", "state": state, "rule_inputs": rule_inputs,
                          "input_sha256": canonical_sha256({"state": state, "rule_inputs": rule_inputs})})
    return items


def match_items(case: Path, existing: list[Any], limit: int = 60) -> list[dict[str, Any]]:
    """New-finding vs existing-claim pairs for an agent-provided list [{id, claim}]."""
    pairs = [(f, e) for f in case_findings(case) for e in existing if isinstance(e, dict)][:limit]
    items = []
    for finding, candidate in pairs:
        state = {"new_claim": text(finding.get("claim")), "existing_claim": text(candidate.get("claim"))}
        items.append({"group": "matches", "target": f"finding:{text(finding.get('id'))}|existing:{text(candidate.get('id'))}",
                      "state": state, "rule_inputs": {}, "input_sha256": canonical_sha256({"state": state, "rule_inputs": {}})})
    return items


def ingest_status(case: Path, entities: list[Any] | None = None, existing: list[Any] | None = None) -> list[dict[str, Any]]:
    """Every current ingest target with its stored result: judged, unavailable, stale or unchecked.

    Knowledge-batch targets are always evaluated; entity and matching targets
    only when the same input lists are supplied. Results stored in the main
    signals file by an earlier layout are reported as legacy and must be rerun.
    """
    stored = load_signals(case, INGEST_SIGNALS_NAME)
    phase = ((stored or {}).get("phases") or {}).get("ingest") or {}
    current = batch_items(case)
    if entities is not None:
        current += entity_items(entities)
    if existing is not None:
        current += match_items(case, existing)
    results = []
    for group, supplied in (("entities", entities), ("matches", existing)):
        if supplied is None and phase.get(group):
            results.append({"group": group, "target": None, "status": "inputs_not_supplied", "mode": phase.get("mode"),
                            "result": None, "flags": [{"reason": "freshness_unknown",
                                                       "detail": f"pass the current {group} input list to check these stored results"}]})
    for item in current:
        by_target = {entry.get("target"): entry for entry in phase.get(item["group"], [])}
        entry = by_target.get(item["target"])
        if item.get("unresolved"):
            status = "unresolved"
        elif entry is None:
            status = "unchecked"
        elif entry.get("input_sha256") != item["input_sha256"]:
            status = "stale"
        else:
            status = entry.get("status")
        results.append({"group": item["group"], "target": item["target"], "status": status, "mode": phase.get("mode"),
                        "result": (entry or {}).get("result") if status in ("judged", "unavailable") else None,
                        "flags": (entry or {}).get("flags", []) if status == "judged" else []})
    main = load_signals(case)
    if ((main or {}).get("phases") or {}).get("ingest"):
        results.append({"group": "legacy", "target": SIGNALS_PATH, "status": "legacy_location", "mode": None, "result": None,
                        "flags": [{"reason": "ingest_results_in_legacy_location",
                                   "detail": f"rerun ingest checks; results now live in {INGEST_SIGNALS_PATH}"}]})
    return results
