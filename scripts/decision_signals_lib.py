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
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

from spotlight_orchestration.case_writer import atomic_write_files
from spotlight_orchestration.contract import OrchestrationError
from spotlight_orchestration.storage import read_data_bytes, transaction

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
        if path is None:
            continue
        document = path.read_text(encoding="utf-8", errors="ignore")
        excerpt = locate_excerpt(document, quote)
        if excerpt:
            located.append({"url": text(source.get("url")), "document_heading": document_heading(document), "excerpt": excerpt})
    if located:
        return located[:MAX_SOURCES], "listed_source"
    research = case / "research"
    if research.is_dir() and _directory_contained(case, research):
        for candidate in sorted(research.rglob("*.md")):
            path = _contained(case, candidate)
            if path is None:
                continue
            document = path.read_text(encoding="utf-8", errors="ignore")
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
    for override in doc.get("overrides", []):
        _require(isinstance(override, dict), "each override must be an object")
    for name, phase in doc["phases"].items():
        _require(name in ("gate1", "report", "ingest") and isinstance(phase, dict), f"invalid phase {name!r}")
        _require(phase.get("mode") in MODES, f"phase {name} has an invalid mode")
        groups = {"gate1": ("findings",), "report": ("items",), "ingest": ("propositions", "memberships", "entities", "matches")}[name]
        for group in groups:
            entries = phase.get(group, [])
            _require(isinstance(entries, list), f"{name}.{group} must be a list")
            for entry in entries:
                _require(isinstance(entry, dict), f"{name}.{group} entries must be objects")
                _require(isinstance(entry.get("input_sha256"), str), f"{name}.{group} entry lacks input_sha256")
                _require(entry.get("status") in ("judged", "routed", "unavailable"), f"{name}.{group} entry has an invalid status")
                _require(isinstance(entry.get("flags", []), list), f"{name}.{group} flags must be a list")
    return doc


def load_signals(case: Path, name: str = SIGNALS_NAME) -> dict[str, Any] | None:
    """None when absent; SignalsError when present but unreadable, malformed or a symlink."""
    case = case.resolve()
    try:
        content = read_data_bytes(case, name)
    except OrchestrationError as exc:
        raise SignalsError(str(exc)) from exc
    if content is None:
        return None
    try:
        doc = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SignalsError(f"data/{name} is not valid JSON: {exc}") from exc
    return validate_signals(doc)


def update_signals(case: Path, name: str, mutate: Callable[[dict[str, Any] | None], dict[str, Any]]) -> dict[str, Any]:
    """Locked read-modify-write through descriptor-anchored, no-follow atomic publication."""
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
            if entry.get("input_sha256") != grounding_fingerprint(case, finding):
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
    findings = json.loads((case / "data" / "findings.json").read_text(encoding="utf-8")).get("findings") or []
    by_id = {text(f.get("id")): f for f in findings if isinstance(f, dict)}
    checks = render.canonical_checks(json.loads((case / "data" / "fact-check.json").read_text(encoding="utf-8")))
    verdicts = {fid: effective_verdict(render, case, f, checks, signals)[0] for fid, f in by_id.items()}
    draft = json.loads((case / "data" / "report-draft.json").read_text(encoding="utf-8"))
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
        items.append({"target": target, "finding_ids": fids, "state": state,
                      "input_sha256": canonical_sha256({"state": state, "finding_ids": fids})})
    return items


# ------------------------------------------------------------------ ingest (knowledge batch)
def batch_items(case: Path) -> list[dict[str, Any]]:
    """Proposition and claim-event checks for data/knowledge-batch.json, shared by producer and consumers.

    Every input that can change the derived result is part of ``state`` or
    ``rule_inputs`` and therefore of the fingerprint.
    """
    batch_path = case / "data" / "knowledge-batch.json"
    if not batch_path.is_file():
        return []
    findings = json.loads((case / "data" / "findings.json").read_text(encoding="utf-8")).get("findings") or []
    by_id = {text(f.get("id")): f for f in findings if isinstance(f, dict)}
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    claims = {c.get("id"): c for c in batch.get("claims", []) if isinstance(c, dict)}
    events = {e.get("id"): e for e in batch.get("events", []) if isinstance(e, dict)}
    items: list[dict[str, Any]] = []

    def item(group: str, target: str, state: dict[str, Any], rule_inputs: dict[str, Any]) -> None:
        items.append({"group": group, "target": target, "state": state, "rule_inputs": rule_inputs,
                      "input_sha256": canonical_sha256({"state": state, "rule_inputs": rule_inputs})})

    for claim in claims.values():
        finding = by_id.get(text((claim.get("origin") or {}).get("finding_id")))
        if finding:
            item("propositions", f"claim:{claim.get('id')}",
                 {"finding_claim": text(finding.get("claim")), "proposition": text(claim.get("proposition"))}, {})
    for membership in batch.get("claim_event_memberships", []):
        if not isinstance(membership, dict):
            continue
        claim = claims.get((membership.get("claim") or {}).get("id"))
        event = events.get((membership.get("event") or {}).get("id"))
        if claim and event:
            item("memberships", f"membership:{membership.get('id')}",
                 {"claim": text(claim.get("proposition")), "event_label": text(event.get("label")), "event_core": event.get("core") or {}},
                 {"declared_relation": text(membership.get("relation"))})
    return items
