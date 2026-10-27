"""Shared, stdlib-only validation for case-local text evidence anchors."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


RLM_LEAD_MARKER = "# RLM-distilled leads from"


def normalized(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


FALLBACK_OUTCOMES = {"succeeded", "failed", "not_attempted"}


def fallback_outstanding(gate: Any) -> bool:
    """True when an evidence item's missing-source gate still lacks its source.

    ``fallback_required`` records that the requested source was not returned,
    so a fallback acquisition was needed; ``fallback_outcome`` records what
    that fallback produced. Only a fallback that succeeded clears the gate.
    """
    if not isinstance(gate, dict) or gate.get("fallback_required") is False:
        return False
    return not (gate.get("fallback_required") is True and gate.get("fallback_outcome") == "succeeded")


def case_evidence_path(case_dir: Path, value: Any) -> Path | None:
    """Resolve a regular artifact only within this case's research/evidence roots."""
    raw = value.strip() if isinstance(value, str) else ""
    if not raw:
        return None
    case_real = case_dir.resolve()
    roots = [(case_dir / name).resolve() for name in ("research", "evidence")]
    supplied = Path(raw).expanduser()
    candidate = supplied if supplied.is_absolute() else case_dir / supplied
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_file() or resolved.is_symlink():
        return None
    try:
        resolved.relative_to(case_real)
    except ValueError:
        return None
    return resolved if any(is_relative_to(resolved, root) for root in roots) else None


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def rlm_lead_failure(path: Path) -> str | None:
    """RLM summaries locate evidence; only their stored raw sidecars may anchor it."""
    try:
        with path.open(errors="replace") as handle:
            opening_lines = [handle.readline() for _ in range(8)]
    except OSError as exc:
        return f"cannot inspect {path.name}: {exc}"
    if any(
        line.lstrip("\ufeff").strip().startswith(RLM_LEAD_MARKER)
        for line in opening_lines
    ):
        raw = path.with_name(path.name + ".raw")
        guidance = f"; cite {raw.name} instead" if raw.is_file() else ""
        return f"{path.name} is an RLM-distilled lead file, not source evidence{guidance}"
    return None


def json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError("must start with '/'")
    current = document
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise ValueError(f"does not resolve at {token!r}")
    return current


def source_lines(path: Path) -> list[str]:
    """The file's lines as `grep -n`, `sed -n` and the Pi read_file tool number them.

    Only a line feed ends a line; a carriage return just before it is dropped
    (CRLF files). Form feeds, vertical tabs, a lone carriage return and the
    Unicode separators stay inside the line, so pdftotext output (a form feed
    at every page break) keeps the line numbers an agent sees with grep.
    """
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        text = handle.read()
    if text == "":
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def legacy_source_lines(path: Path) -> list[str]:
    """The numbering anchors used before ``source_lines``: Python's universal
    newlines and ``str.splitlines()``, where a form feed, vertical tab, file or
    group separator, NEL or Unicode line separator also ends a line."""
    return path.read_text(errors="replace").splitlines()


def selected_source_texts(path: Path, source_ref: dict[str, Any]) -> tuple[list[str], str | None]:
    """Every text a source_ref may select: the ``source_lines`` selection
    first, then, for a line range, the selection under the legacy numbering
    when it differs. An anchor recorded before the numbering changed stays
    valid as long as its text matches exactly what its own numbering selects;
    no anchor is ever matched loosely."""
    pointer = source_ref.get("json_pointer")
    if isinstance(pointer, str):
        try:
            document = json.loads(path.read_text())
            value = json_pointer(document, pointer)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError) as exc:
            return [], f"JSON pointer {pointer!r} in {path.name} {exc}"
        if isinstance(value, str):
            return [value], None
        return [json.dumps(value, ensure_ascii=False, sort_keys=True)], None

    start = source_ref.get("line_start")
    end = source_ref.get("line_end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
        return [], "source_ref needs a valid line_start/line_end or json_pointer"
    try:
        numberings = (source_lines(path), legacy_source_lines(path))
    except OSError as exc:
        return [], f"cannot read {path.name}: {exc}"
    candidates: list[str] = []
    for lines in numberings:
        if end <= len(lines):
            selected = "\n".join(lines[start - 1:end])
            if selected not in candidates:
                candidates.append(selected)
    if not candidates:
        return [], f"source_ref lines {start}-{end} exceed {path.name} ({len(numberings[0])} lines)"
    return candidates, None


def quote_variants(quote: Any) -> list[str]:
    """The normalized quote, then, when it differs, the quote with each literal
    \\n (the two characters a model can write for a line break, 12B r3 cc and
    gold sens4 marlow) read as a line break: the rule the passage writer uses."""
    text = quote if isinstance(quote, str) else ""
    variants = [normalized(text)]
    if "\\n" in text and normalized(text.replace("\\n", "\n")) not in variants:
        variants.append(normalized(text.replace("\\n", "\n")))
    return [variant for variant in variants if variant]


def locate_quote(path: Path, quote: Any) -> tuple[int, int] | None:
    """The one line range of ``path`` (as source_lines numbers it) that holds
    the quote, when the quote occurs exactly once in the file; otherwise None.
    The range is the smallest one, from the first to the last line the quote
    touches. A quote found as written is never retried with \\n read as a line
    break, so an ambiguous quote stays unlocated."""
    try:
        lines = source_lines(path)
    except OSError:
        return None

    def holds(start: int, end: int) -> bool:
        return wanted in normalized("\n".join(lines[start:end]))

    whole = normalized("\n".join(lines))
    for wanted in quote_variants(quote):
        first = whole.find(wanted)
        if first == -1:
            continue
        if whole.find(wanted, first + 1) != -1:
            return None
        # Holding the quote only grows with the range, so search both ends.
        low, high = 1, len(lines)
        while low < high:  # the fewest leading lines that hold it
            middle = (low + high) // 2
            low, high = (low, middle) if holds(0, middle) else (middle + 1, high)
        end = low
        low, high = 0, end - 1
        while low < high:  # the latest start that still holds it
            middle = (low + high + 1) // 2
            low, high = (middle, high) if holds(middle, end) else (low, middle - 1)
        return low + 1, end
    return None


def validate_source_ref(
    case_dir: Path,
    source_ref: Any,
    quote: Any,
) -> tuple[set[Path], list[str]]:
    if not isinstance(source_ref, dict):
        return set(), ["source_ref must be an object"]
    path = case_evidence_path(case_dir, source_ref.get("path"))
    if path is None:
        return set(), [
            f"source_ref path {source_ref.get('path')!r} does not resolve to a case-local file"
        ]
    lead_failure = rlm_lead_failure(path)
    if lead_failure:
        return {path}, [lead_failure]
    candidates, error = selected_source_texts(path, source_ref)
    if error:
        return {path}, [error]
    variants = quote_variants(quote)
    if variants and not any(wanted in normalized(selected) for wanted in variants for selected in candidates):
        return {path}, [f"exact quote is not present in the selected source_ref from {path.name}"]
    return {path}, []
