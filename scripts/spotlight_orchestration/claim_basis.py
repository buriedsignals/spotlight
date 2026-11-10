"""What a fact-check verdict rests on, counted from the record (stdlib only).

The fact-checker judges a claim; this module only counts what it recorded, so
a review page can say what a verdict rests on without asking a model again:

- how many independent sources support the claim. Two pages of one site, or a
  page and its Wayback Machine copy, are one source (a registrable-domain
  family), so corroboration is counted rather than asserted;
- whether a supporting source is a government host, which a single-source
  verdict may legitimately rest on (an official record);
- for a claim that is not verified, whether the fact-check recorded any
  evidence at all, so "not checked" does not read as "checked and unsupported";
- whether the claim is long enough that it probably bundles several assertions.

The research these rules follow is credited in docs/fact-checking.md.
"""

from __future__ import annotations

import re
import sys
from typing import Any, Mapping
from urllib.parse import urlparse

from .contract import SCRIPT_DIR

_DECISIONS = SCRIPT_DIR.parent / "integrations" / "decisions"
if str(_DECISIONS) not in sys.path:
    sys.path.insert(0, str(_DECISIONS))
from research_rules import registrable_family  # noqa: E402

# Verified claims in the fact-checker gold set (219 claims from local runs,
# 2026-11-10) were not fully supported 19% of the time under 40 words and 41%
# of the time from 40 words up.
LONG_CLAIM_WORDS = 40

_URL = re.compile(r"https?://[^\s)\]>\"']+")
_WAYBACK = re.compile(r"^https?://web\.archive\.org/web/\d+[a-z_]*/(https?://.+)$", re.I)
# Host suffixes that governments and intergovernmental bodies reserve.
_GOVERNMENT_HOST = re.compile(r"(?:^|\.)(?:gov|mil|int|gov\.[a-z]{2}|gouv\.fr|admin\.ch|europa\.eu|gc\.ca|bund\.de)$")


def _source_url(item: Any) -> str:
    """The URL an evidence item cites, read through a Wayback Machine copy."""
    source = item.get("source") if isinstance(item, Mapping) else None
    match = _URL.search(source) if isinstance(source, str) else None
    if not match:
        return ""
    wrapped = _WAYBACK.match(match.group(0))
    return wrapped.group(1) if wrapped else match.group(0)


def _families(items: Any) -> tuple[list[str], bool]:
    """Distinct source families in evidence order, and whether one is a government host."""
    families: list[str] = []
    government = False
    for item in items if isinstance(items, list) else []:
        url = _source_url(item)
        family = registrable_family(url)
        if not family:
            continue
        if family not in families:
            families.append(family)
        government = government or bool(_GOVERNMENT_HOST.search((urlparse(url).hostname or "").lower()))
    return families, government


def _named(families: list[str]) -> str:
    shown = ", ".join(families[:3])
    return f"{shown} and {len(families) - 3} more" if len(families) > 3 else shown


def _count(items: Any) -> int:
    return sum(1 for item in items if isinstance(item, Mapping)) if isinstance(items, list) else 0


def claim_basis(claim: Mapping[str, Any] | None, claim_text: str = "") -> dict[str, Any]:
    """The basis of one fact-check claim: ``kind``, a one-line ``label`` and, for a long claim, a ``note``."""
    claim = claim if isinstance(claim, Mapping) else {}
    verdict = claim.get("verdict")
    families, government = _families(claim.get("evidence_for"))
    against = _count(claim.get("evidence_against"))
    supporting = _count(claim.get("evidence_for"))
    if verdict == "verified":
        if government:
            kind, label = "government_source", f"Rests on a government source ({_named(families)})."
        elif len(families) >= 2:
            kind, label = "independent_sources", f"Rests on {len(families)} independent sources ({_named(families)})."
        elif families:
            kind, label = "single_source", f"Rests on one source ({families[0]})."
        else:
            kind, label = "no_source", "The fact-check records no source URL for this verdict."
    elif not supporting and not against:
        kind, label = "not_checked", "Not checked: the fact-check recorded no evidence for or against this claim."
    elif against:
        kind = "evidence_against"
        label = f"Checked: the fact-check recorded evidence against this claim ({against} item{'s' if against != 1 else ''})."
    else:
        kind = "unconfirmed"
        where = f" from {_named(families)}" if families else ""
        label = f"Checked: supporting evidence is on record{where}, but the fact-check did not verify the claim."
    words = len(claim_text.split()) if isinstance(claim_text, str) else 0
    note = f"Long claim ({words} words): it may bundle several assertions that need separate checks." if words >= LONG_CLAIM_WORDS else ""
    return {"kind": kind, "label": label, "note": note, "source_families": families}
