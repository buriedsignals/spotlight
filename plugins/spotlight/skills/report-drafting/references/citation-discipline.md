# Citation discipline (hard rule — learned the hard way)

**The synthesis layer must NEVER originate a primary-source citation.** Every UUID, every external URL, every filing reference, every direct quote MUST be copied verbatim from a ground-truth file written by an earlier phase. If a citation is not already in the trail, do not invent it — go fetch it.

For activated Spotlight cases, direct quotations are stricter: select only an
`expression_id` in `report-draft.json`. Do not retype passage text or attribution.
The renderer owns the published quotation and resolves it from the validated active
record in `data/source-expressions.json`. These structural checks prove passage
integrity and traceability, not semantic entailment; fact-check and human editorial
review remain responsible for whether the framing is justified.

This is the same class of rule as Firecrawl-only. The failure mode it prevents: a synthesis pass that "looks right" but contains URLs and UUIDs the LLM generated from semantic memory, that 404 or resolve to the wrong filing under adversarial review. This is the most common way investigative-journalism submissions get killed.

## Build the per-finding citation manifest FIRST

Before drafting any finding, extract its allowed citation set:
- From data-detective: `findings.json` `supporting_cards` + `external_sources` + `supporting_query_hashes`.
- From spotlight (if `promoted_from` is set): `case-trace/spotlight/results/<OS-NNN>/data/findings.json` `external_sources` + `research/*.md` filenames + `investigation-log.json` `urls_accessed`.
- Write the manifest to `/tmp/c-NNN-citations.txt` — this is the ALLOWED set for this finding. **Any URL or UUID in the draft must appear in this file. No exceptions.**

## Sources of truth (in priority order)

1. `case-trace/spotlight/results/*/research/*.md` — the literal scraped page text. URL of the original is in the filename or the file's frontmatter or `investigation-log.json` under `urls_accessed`.
2. `case-trace/spotlight/results/*/data/findings.json` — the investigator's curated source list per finding (`external_sources` arrays).
3. `case-trace/data-detective/cards/senate_filing_<UUID>.md` — evidence cards for primary filings, generated deterministically from the DuckDB index. The UUID in the filename IS the canonical UUID.
4. `case-trace/data-detective/anomalies/*.provenance.json` — SQL hashes and detector SQL.
5. `case-trace/data-detective/external/factcheck/*` — adversarial fact-checker archives.

## Required before any external URL or UUID lands in the draft

```bash
# Pattern A — UUID is a Senate LDA filing
grep -rln "<UUID>" case-trace/spotlight/results/ case-trace/data-detective/cards/
# Must return at least one ground-truth file. If empty: STOP. Do not paste this UUID.

# Pattern B — external URL (news article, gov page, etc.)
grep -rln "<URL>" case-trace/spotlight/results/
# Must return at least one ground-truth file. If empty AND the URL is not already in
# case-trace/data-detective/external/, STOP.
# To add a URL: scrape it first (fetch seam), write under case-trace/data-detective/external/, then it is grep-able.
```

If a fact has no ground-truth file, either **drop the claim** (synthesis documents what was verified upstream; it does not introduce new facts) or **fetch it** (one-shot scrape → `case-trace/data-detective/external/<slug>.md` → cite). Never paraphrase or "remember" a URL.

## What NOT to do

- ❌ Guess a `nytimes.com/<y>/<m>/<d>/<slug>.html` URL — NYT URLs aren't predictable from the headline. **Look it up.**
- ❌ Pick a plausible-looking UUID for an LDA filing. UUIDs aren't predictable. **Grep the scrape.**
- ❌ Invent timing ("retained the day after the indictment"). The LD-1 has an effective date; if you haven't read it, don't assert it.
- ❌ Re-derive a court case name / docket caption from memory. Pull it from the archived docket text.

## Final pre-commit closure script

Before declaring P5 complete:

```bash
# Extract every UUID and external URL from the three drafted files
grep -ohE '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}|https?://[^[:space:])"]+' \
  case/findings-report.md case/report.html case/evidence-map.json \
  | sort -u > /tmp/p5-citations.txt

# For each, confirm it appears in ground-truth files
while read -r token; do
  if ! grep -rlq -- "$token" case-trace/spotlight/results/ case-trace/data-detective/cards/ case-trace/data-detective/external/ case-trace/data-detective/anomalies/; then
    echo "ORPHAN CITATION: $token"
  fi
done < /tmp/p5-citations.txt
```

An orphan citation is a P5 bug. Fix it before declaring complete — fetch the source or remove the claim.

## Audit breadcrumbs

When you correct a previously-published citation, leave a trail (this is what makes the case-trace defensible — not "we never erred" but "we caught and corrected, with the trail in the artifact"):

```json
"description": "Akin Gump LDA filing for Ant Group: UUID a4411100-... (Q1 2025 LD-2). Previous version cited UUID 3a6e17c0-... in error — that resolves to a Posco America filing. Corrected against Spotlight OS-002 archive at case-trace/spotlight/results/OS-002.../research/lda-akingump-antgroup-filing.md."
```
