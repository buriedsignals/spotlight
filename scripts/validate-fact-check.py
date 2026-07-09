#!/usr/bin/env python3
"""Fact-check evidence validator — verification rigor, enforced deterministically.

A `status:"verified"` verdict is only as good as its evidence trail. A small local
fact-checker will sometimes FABRICATE the trail (grounded 2026-07-09, gold-inv-ef-0:
a verdict cited "corporate registry (Zefix)" as confirmation when the in-run Zefix
fetch returned a bot-wall, and "official bylaws" that were never fetched). This
validator makes that structurally impossible to pass:

  ANCHOR  every verified verdict's finding must cite on-disk source files
          (findings.json sources[].local_file) that exist AND contain the claim's
          key terms — a claim nothing on disk supports cannot be "verified".
  METHOD  every source the verdict NAMES in verification_evidence that maps to a
          research file (by name-stem) must itself support the claim — citing a
          bot-walled or empty fetch as confirmation is a hard fail.

Usage:
  python3 scripts/validate-fact-check.py <CASE_DIR> [--json]

Exit codes: 0 = all verified verdicts anchored; 3 = at least one failure (report on
stdout, one line per verdict). Non-"verified" statuses (unverified/disputed/...) are
never failed for lacking evidence — honesty about uncertainty is the desired behavior.

Dual use: (a) run at the fact-check gate — the orchestrator bounces failures back to
the fact-checker ONCE with the reasons; (b) gold-dataset filter — a trajectory only
enters the tune set if its fact-check passes (tools/fine-tuning, v4 data QA).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "has", "have", "had", "its", "their",
    "and", "or", "of", "in", "on", "at", "as", "by", "to", "for", "with", "from",
    "that", "this", "these", "those", "it", "be", "been", "which", "who", "whose",
    "current", "currently", "also", "both", "including", "organized", "organised",
    "structured", "headquartered", "located", "based", "known", "named",
    # verification-speak that must not be mistaken for source identifiers
    "confirmed", "verified", "official", "documentation", "cross", "reference",
    "registry", "corporate", "source", "sources", "via",
}


def claim_terms(claim: str) -> list[str]:
    """Key terms for the ANCHOR check: capitalized runs (entities) + distinctive
    long words. Both matter — 'Zug'/'Ethereum Foundation' anchor the who/where,
    'volunteer'/'council' anchor the what (the part small models hallucinate)."""
    entities = re.findall(r"\b(?:[A-Z][\w''-]+(?:\s+[A-Z][\w''-]+)*)\b", claim)
    # Strip leading stopword tokens from entity phrases ("The Ethereum Foundation" →
    # "Ethereum Foundation") so sentence-initial capitalization doesn't poison matching.
    entities = [" ".join(w for i, w in enumerate(e.split())
                         if i > 0 or w.lower() not in STOPWORDS) or e
                for e in entities]
    entities = [e for e in entities if e.lower() not in STOPWORDS and len(e) > 2]
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z''-]{4,}", claim)]
    words = [w for w in words if w not in STOPWORDS]
    # Adjacent content-word BIGRAMS are the discriminating unit for compound claims —
    # 'council' and 'volunteer' appearing separately somewhere is not "a volunteer
    # council" (grounded: FC2's hallucinated governance phrase slipped a bag-of-words
    # check). Matching is punctuation-normalized, so "Zug, Switzerland" ≈ "Zug Switzerland".
    content = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z''-]+", claim)
               if w.lower() not in STOPWORDS]
    bigrams = [f"{a} {b}" for a, b in zip(content, content[1:])]
    seen, terms = set(), []
    for t in entities + words + bigrams:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            terms.append(t)
    return terms


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", " ", s.lower()))


def read_corpus(paths: list[Path]) -> str:
    """A cited file and its `.raw` provenance sidecar are ONE acquired source: the
    leads file is a lossy RLM distillation (grounded: the e4b dropped 'Zug' from the
    Wikipedia leads while the .raw contains it), so evidence checks must consult
    both — that is precisely what the sidecar exists for."""
    out = []
    for p in paths:
        for f in (p, p.with_name(p.name + ".raw")):
            try:
                out.append(f.read_text(errors="replace").lower())
            except OSError:
                pass
    return "\n".join(out)


def term_coverage(terms: list[str], corpus: str) -> tuple[float, list[str]]:
    if not terms:
        return 0.0, []
    corpus = _norm(corpus)
    missing = [t for t in terms if _norm(t) not in corpus]
    return 1 - len(missing) / len(terms), missing


def name_stems(text: str) -> list[str]:
    """Source-ish identifiers named in verification_evidence: capitalized words and
    domain-like tokens, lowercased, for fuzzy matching against research filenames."""
    toks = re.findall(r"\b[A-Z][A-Za-z0-9-]{2,}\b|\b[\w-]+\.(?:md|pdf|json|txt)\b", text)
    return sorted({t.lower().removesuffix(".md") for t in toks if t.lower() not in STOPWORDS})


def validate(case_dir: Path) -> tuple[list[dict], int]:
    data = case_dir / "data"
    fc_path = data / "fact-check.json"
    fd_path = data / "findings.json"
    if not fc_path.exists():
        return [{"id": "-", "ok": False, "reason": f"missing {fc_path}"}], 3

    fact_checks = json.loads(fc_path.read_text()).get("fact_checks", [])
    findings = {}
    if fd_path.exists():
        findings = {f.get("id"): f for f in json.loads(fd_path.read_text()).get("findings", [])}

    # Research corpus files, keyed by name stem, for METHOD-check mapping.
    research_files = {
        p.name.lower(): p
        for d in (case_dir / "research", case_dir / "evidence")
        if d.is_dir()
        for p in d.rglob("*")
        if p.is_file() and not p.name.endswith(".raw")
    }

    # Discriminative-term filter: the case's subject entity ("Ethereum Foundation")
    # appears in nearly every research file, so it carries no verification power —
    # coverage must be earned on the claim's DISTINCTIVE terms (the ones a small
    # model hallucinates: "volunteer council", "Zug"). A term present in >70% of
    # research files is dropped from the denominator (unless nothing else remains).
    file_texts = {p: read_corpus([p]) for p in research_files.values()}

    def distinctive(terms: list[str]) -> list[str]:
        if not file_texts:
            return terms
        n = len(file_texts)
        kept = [t for t in terms
                if sum(t.lower() in txt for txt in file_texts.values()) <= 0.7 * n]
        return kept or terms

    results, failed = [], 0
    for fc in fact_checks:
        vid = fc.get("id", "?")
        if fc.get("status") != "verified":
            results.append({"id": vid, "ok": True, "reason": f"status={fc.get('status')} (not checked)"})
            continue

        claim = fc.get("claim", "")
        terms = distinctive(claim_terms(claim))
        reasons = []

        # ANCHOR — the linked finding's on-disk sources must exist and support the claim.
        finding = findings.get(fc.get("finding_id"))
        anchors = []
        if finding:
            for s in finding.get("sources", []):
                lf = s.get("local_file")
                if lf:
                    p = Path(lf)
                    if not p.is_absolute():
                        p = case_dir / lf
                    if p.exists():
                        anchors.append(p)
        if not anchors:
            reasons.append("no existing on-disk source file behind the linked finding "
                           f"(finding_id={fc.get('finding_id')}) — a verified verdict needs a local evidence trail")
        else:
            cov, missing = term_coverage(terms, read_corpus(anchors))
            if cov < 0.75 or (terms and len(missing) == len(terms)):
                reasons.append(
                    f"cited source files do not support the claim (term coverage {cov:.0%}; "
                    f"missing: {', '.join(missing[:5])}) — files: {', '.join(a.name for a in anchors)}")

        # METHOD — sources NAMED in the evidence string that map to research files
        # must themselves contain the claim; citing a bot-wall/empty fetch is a fail.
        evidence_text = fc.get("verification_evidence", "") or ""
        for stem in name_stems(evidence_text):
            # An honestly-disclaimed source ("the Zefix search returned a bot-wall and
            # could NOT be used") is the DESIRED behavior — skip stems whose mention
            # sits in a negation window instead of failing them.
            lowered = evidence_text.lower()
            idx = lowered.find(stem)
            window = lowered[max(0, idx - 120): idx + len(stem) + 120]
            if re.search(r"\b(not|no|never|couldn'?t|could not|failed|unable|unavailable|"
                         r"bot-?wall(?:ed)?|excluded|rejected|blocked|empty)\b", window):
                continue
            hits = [p for name, p in research_files.items() if stem in name]
            for p in hits:
                # Same bar as ANCHOR: a page that merely ECHOES the entity name (a
                # bot-wall echoing the search query, an empty result page) must not
                # count as confirmation — grounded on gold-inv-ef-0 FC1/Zefix (40%
                # coverage from query echo alone, zero distinctive claim terms). Bar 0.75:
                # a false fail costs one bounce with explicit reasons — healthy discipline.
                cov, _ = term_coverage(terms, read_corpus([p]))
                if cov < 0.75:
                    reasons.append(
                        f"verification_evidence cites '{stem}' → {p.name}, but that file does not "
                        f"support the claim (term coverage {cov:.0%}) — likely a failed/bot-walled "
                        "fetch cited as confirmation; mark unverified or re-acquire the source")

        ok = not reasons
        failed += 0 if ok else 1
        results.append({"id": vid, "ok": ok,
                        "reason": "anchored" if ok else "; ".join(reasons),
                        "claim": claim})
    return results, (3 if failed else 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir", help="the case directory (contains data/, research/)")
    ap.add_argument("--json", action="store_true", help="emit the full report as JSON")
    args = ap.parse_args()

    results, code = validate(Path(args.case_dir))
    if args.json:
        print(json.dumps({"ok": code == 0, "verdicts": results}, indent=2))
    else:
        for r in results:
            print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['id']}: {r['reason']}")
        print(f"\nfact-check evidence: {'OK' if code == 0 else 'FAILED — bounce to the fact-checker with the reasons above'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
