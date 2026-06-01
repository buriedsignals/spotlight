---
name: report-drafting
description: "Phase 5 synthesis sub-skill for data-detective — draft the journalist-grade deliverables editors actually read: findings-report.md (audit narrative), report.html (public-facing artifact), evidence-map.json (machine-readable ledger). Ships with a working report-template.html skeleton (CSS variables, pill classes, .path/.sources/.flag/.timeline/.phase patterns) and the design discipline that produces it: per-finding replication-path block, per-finding sources strip, novelty + confidence pills, phase-by-phase methodology absorbing the fact-check verdict table inside Phase 3 and the spotlight-handoff table inside Phase 6, and HTML editing protocol that never greedy-regexes the live file. Use at Phase 5 after Gate 1, before Phase 6 spotlight-handoff. Triggers on draft the report, build the HTML report, journalist-grade output, synthesis phase, findings report, or evidence map."
license: MIT
metadata:
  type: orchestration-subskill
  parent: data-detective
  phase: 5
invocable_by: [orchestrator, user]
---

# report-drafting — Phase 5 synthesis

You are at Phase 5. Gate 1 has approved a set of verified findings. Your job is to ship the three deliverables editors actually read.

This skill instructs; you execute. Read `references/report-template.html` before drafting — it is the working skeleton.

---

## Required AI assistance notice

Every `report.html` MUST include this notice at the top of the page, after the byline/header material and before the TL;DR findings table:

> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

Use the template's `.honesty` block for this notice. Do not rewrite or soften the responsibility language.

---

## Deliverables (all three required)

| File | Audience | What it is |
|---|---|---|
| `case/findings-report.md` | Editor / fact-checker | Narrative audit document. One section per finding. Plain prose + tables. The authoritative claim-by-claim record. |
| `case/report.html` | Publication / external reader | Public-facing journalism artifact. Styled, scannable, with inline replication paths and archived primary sources. |
| `case/evidence-map.json` | Audit / replication | Machine-readable ledger: claim → cards → query hashes → external URLs. See `references/evidence-map-format.md` in the data-detective skill. |

The HTML is not a markdown render. It is a designed document. Build it from the template, not from scratch.

---

## Required structure per finding (HTML)

Every `<section class="finding">` MUST contain, in order:

1. **Header row** — `<h2>` with embedded finding ID + `.pill-novel` (purple, for genuinely new evidence) OR `.pill-connected` (outline, for new framings of public facts), plus `.pill-high` / `.pill-med` / `.pill-low` for confidence.
2. **Deck** — one-line subhed under the H2 in `<p class="deck">` (≤60ch).
3. **Stats grid** (optional) — `<div class="stats">` for findings with quantitative spine.
4. **Body paragraphs** — `<p>` (auto-constrained to ≤72ch via column width).
5. **`<div class="path" aria-label="How we got here">`** — REPLICATION PATH. One `.step` + `.what` pair per phase that produced this finding. Cite SQL hashes, script paths, archived URLs. This block is what makes the finding auditable in under a minute. **Mandatory.**
6. **`<div class="sources">`** — primary-source URLs with archive references. **Mandatory.**

Optional add-ins:
- `<div class="flag">` for legal qualifications — use `<span class="flag-label">` for the in-line label, NOT `<strong style="display:block">`.
- `<div class="timeline">` for chronological evidence chains (4-column grid: date, event, source).
- `<div class="pull">` for a 1-2 sentence pull quote inside the finding body.

---

## Methodology section pattern (the highest-leverage learning)

The methodology section serves a dual purpose: it documents the skill (the algorithm) AND it logs the actual run. It is NOT a separate generic methodology. It is the audit trail of THIS investigation, in phase order.

Structure: one `<div class="phase">` per phase (P0 through P7).

**Critical:** do NOT break the adversarial fact-check verdict table and the spotlight-handoff outcomes table into separate top-level sections. They read out of phase order. Instead:

- Adversarial fact-check verdicts table → INSIDE the Phase 3 `<div class="phase">`.
- Spotlight-handoff outcomes table (briefs OS-001..OS-N, what they did, what they promoted) → INSIDE the Phase 6 `<div class="phase">`.

This way a reader scrolling the methodology gets the full run in phase order: ingest → resolve → detect+factcheck → gate → synthesize → handoff → vault. Past investigations broke these out and the result read out-of-order; the restructuring at the end is what gave the report its final shape.

---

## Design discipline

CSS variables already in the template (`--ink`, `--paper`, `--rule`, `--bg-soft`, `--red`, `--mono`, `--sans`, `--serif`). Do not reinvent them per finding.

**Max-width rules** (the template enforces these; do not override per-element):
- `h1` → 28ch
- `h2` → 32ch
- `.deck` (subhed) → 60ch
- Body `<p>` → constrained by the column, max-width:none
- `.lede` → constrained by the column, max-width:none
- Tables / `.stats` / `.path` / `.sources` / `.flag` / `.timeline` / `.phase` → full column width, no max-width

**Pill semantics:**
- `.pill-novel` (purple background) — genuinely new evidence not previously published.
- `.pill-connected` (outline) — new framing of public facts via cross-corpus join.
- `.pill-verified` (green) — fact-checker confirmed.
- `.pill-partial` (amber) — fact-checker partial verdict.
- `.pill-high` / `.pill-med` / `.pill-low` — confidence levels.
- `.pill-id` (mono, light) — finding ID badge.

**TL;DR table** at the top: one row per finding, `<a href="#c-NNN">` linked, with novelty + confidence pills inline.

---

## HTML editing protocol (hard rule)

NEVER run greedy regex substitution on the HTML file. Use `Read` + `Edit` with anchored old_strings only. A greedy `re.sub` destroyed the entire report.html mid-pass in a prior investigation and forced a full rebuild.

Specifically:
- For per-finding additions: anchor `old_string` on the closing element of the prior block + the opening of the target block.
- For methodology restructuring: extract the existing section, rewrite as a single block, replace with one `Edit` call.
- If you must regex, do it in a one-shot Python script that prints the diff first, never `re.sub(..., re.DOTALL)` on the whole file.

---

## Citation discipline (hard rule — learned the hard way)

**The synthesis layer must NEVER originate a primary-source citation.** Every UUID, every external URL, every filing reference, every direct quote MUST be copied verbatim from a ground-truth file written by an earlier phase. If a citation is not already in the trail, do not invent it — go fetch it.

This is the same class of rule as Firecrawl-only. The failure mode it prevents: a synthesis pass that "looks right" but contains URLs and UUIDs the LLM generated from semantic memory, that 404 or resolve to the wrong filing under adversarial review. This is the most common way investigative-journalism submissions get killed.

### Sources of truth (in priority order)

1. `case-trace/spotlight/results/*/research/*.md` — the literal scraped page text. URL of the original is in the filename or in the file's frontmatter or in `case-trace/spotlight/results/*/investigation-log.json` under `urls_accessed`.
2. `case-trace/spotlight/results/*/data/findings.json` — the Spotlight investigator's curated source list per finding (the `external_sources` arrays).
3. `case-trace/data-detective/cards/senate_filing_<UUID>.md` — evidence cards for primary filings, generated deterministically from the DuckDB index. The UUID in the filename IS the canonical UUID.
4. `case-trace/data-detective/anomalies/*.provenance.json` — SQL hashes and detector SQL.
5. `case-trace/data-detective/external/factcheck/*` — adversarial fact-checker archives.

### Required before any external URL or UUID lands in the draft

For each citation, run a verification step:

```bash
# Pattern A — UUID is a Senate LDA filing
grep -rln "<UUID>" case-trace/spotlight/results/ case-trace/data-detective/cards/
# Must return at least one ground-truth file. If empty: STOP. Do not paste this UUID into the draft.

# Pattern B — external URL (news article, gov page, etc.)
grep -rln "<URL>" case-trace/spotlight/results/
# Must return at least one ground-truth file. If empty AND the URL is not already in case-trace/data-detective/external/, STOP.
# To add a new URL: firecrawl-scrape it first, write the result under case-trace/data-detective/external/, then it is grep-able.
```

If a fact you want to cite has no ground-truth file, you have two options:
- **Option 1**: drop the claim from the draft. The synthesis layer documents what was verified upstream — it does not introduce new facts.
- **Option 2**: spawn a one-shot firecrawl scrape, write the result to `case-trace/data-detective/external/<slug>.md`, then cite it. Never paraphrase or "remember" a URL.

### What NOT to do

- ❌ "I'll cite the NYT story on X" → write a `nytimes.com/<year>/<month>/<day>/<section>/<slug>.html` guess. NYT URLs are not predictable from headline. **Look it up.**
- ❌ "The Akin Gump × Ant Group filing is somewhere in the LDA database" → pick a plausible-looking UUID. UUIDs are not predictable. **Grep the Spotlight scrape.**
- ❌ "Apple was retained the day after the indictment" → invent timing. The LD-1 has an effective date; if you haven't read it, do not assert "day after".
- ❌ Re-derive a court case name or docket caption from memory ("Bass Berry & Sims is counsel"). Pull it from the docket text the upstream agent archived.

### Final pre-commit check

Before declaring P5 complete, run a closure script:

```bash
# Extract every UUID and external URL from the three drafted files
grep -ohE '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}|https?://[^[:space:])"]+' \
  case/findings-report.md case/report.html case/evidence-map.json \
  | sort -u > /tmp/p5-citations.txt

# For each one, confirm it appears in ground-truth files
while read -r token; do
  if ! grep -rlq -- "$token" case-trace/spotlight/results/ case-trace/data-detective/cards/ case-trace/data-detective/external/ case-trace/data-detective/anomalies/; do
    echo "ORPHAN CITATION: $token"
  fi
done < /tmp/p5-citations.txt
```

An orphan citation is a P5 bug. Fix it before declaring P5 complete — either by fetching the source (firecrawl scrape into `external/`) or by removing the claim from the draft.

### Audit breadcrumbs

When you correct a previously-published citation, leave a trail:

```json
"description": "Akin Gump LDA filing for Ant Group: UUID a4411100-... (Q1 2025 LD-2). Previous version of this entry cited UUID 3a6e17c0-... in error — that UUID resolves to a Posco America filing. Citation corrected against Spotlight OS-002 evidence archive at case-trace/spotlight/results/OS-002.../research/lda-akingump-antgroup-filing.md."
```

This is what makes the case-trace defensible to an editor: not "we never made mistakes" but "we caught and corrected the ones we made, with the audit trail in the artifact."

---

## Workflow

```
1. Read references/report-template.html. Copy to case/report.html.
2. Fill the header (title, deck, byline, lede), keep the required AI assistance notice directly below it, and fill the TL;DR table from findings.json.
3. BEFORE drafting any finding, extract its citation manifest:
   - From data-detective: findings.json supporting_cards + external_sources + supporting_query_hashes
   - From spotlight (if promoted_from is set): case-trace/spotlight/results/<OS-NNN>/data/findings.json external_sources + research/*.md filenames + investigation-log.json urls_accessed
   - Write the manifest to /tmp/c-NNN-citations.txt — this is the ALLOWED set for this finding.
   - Any URL or UUID you want to put in the draft must appear in this file. No exceptions.
4. For each verified finding in findings.json:
   a. Drop a <section class="finding"> from the template's finding-stub block.
   b. Fill the H2 + pills (novelty inferred from finding's promoted_from + corroboration; confidence from fact-check verdict).
   c. Write the body (3-6 paragraphs, prose). Quote primary-source text via Read of the archived page, never paraphrase from memory.
   d. Insert the .path block — one .step+.what pair per phase that produced this finding. Cite SQL hashes from anomalies/*/provenance.json. Cite scripts. Cite archived URLs from /tmp/c-NNN-citations.txt only.
   e. Insert the .sources strip — primary-source URLs only (not secondary commentary), all from the citation manifest.
5. Fill methodology section:
   a. One .phase block per executed phase (P0..P7).
   b. INSIDE Phase 3: adversarial fact-check verdict table.
   c. INSIDE Phase 6: spotlight-handoff outcomes table.
6. Fill "Open monitoring targets" section from findings.json's unresolved-gaps list.
7. Fill footer: conflicts of interest, database attributions.
8. Write findings-report.md in parallel (narrative form, no styling, every claim sourced from the same allowed-set manifest as the HTML).
9. Write evidence-map.json (audit ledger, see data-detective/references/evidence-map-format.md).
10. RUN THE CITATION CLOSURE SCRIPT (see Citation Discipline section above). Every UUID and external URL in the three drafted files must trace to a ground-truth file. Fix orphans before proceeding.
11. Validate HTML tags balance via:
     python3 -c "from html.parser import HTMLParser; ..." 
12. Open report.html in browser; visual smoke test.
13. Append synthesis_complete + draft_paths + citation_closure_passed to investigation-log.json.
```

---

## Inputs / Outputs

**Reads:**
- `case/data/findings.json` (verified claims)
- `case/data/fact-check.json` (adversarial verdicts)
- `case/data/investigation-log.json` (phase log + handoff outcomes)
- `case/anomalies/*/provenance.json` (SQL hashes for path blocks)
- `case-trace/spotlight/results/*/` (OSINT investigation outputs to summarize in Phase 6 table)

**Writes:**
- `case/findings-report.md`
- `case/report.html`
- `case/evidence-map.json`

---

## Anti-patterns (learned the hard way)

- **Wall-of-text findings.** Every finding gets a `.path` block. If you find yourself writing "we ran D11 then drilled then archived three URLs" in the prose, lift it out into the path block.
- **Methodology-at-the-bottom dumping ground.** The methodology section is the run log; phases must appear in phase order, with fact-check and handoff tables INSIDE the relevant phase blocks. Not separate.
- **Sources at the end of the document.** Sources go inline per-finding via `.sources` strip. Readers should never have to scroll to a bibliography.
- **`.flag strong { display: block }`.** Breaks inline legal citations to new lines. Use `<span class="flag-label">` instead.
- **Markdown-style HTML.** The HTML is a designed document, not a markdown render. Tables, grids, pill systems, two-column blocks are the point. If your HTML reads like a `pandoc` output, restart from the template.
- **Regex on the live file.** Use `Read` + `Edit` with anchored old_strings. A greedy substitution will destroy hours of work.
- **Citation hallucination.** The synthesis layer must never originate a UUID, URL, court case docket caption, or direct quote that doesn't appear in the case-trace ground-truth files. See "Citation discipline" above. The failure mode this prevents — a draft that looks right but cites URLs that 404 or UUIDs that resolve to the wrong filing — is the single most common way investigative-journalism submissions get killed. Run the closure script before declaring done.
- **Novelty inflation.** If a finding's core claim has already been published by a mainstream outlet (NYT, ProPublica, WaPo, Reuters, etc.), it gets `.pill-connected` (outline), not `.pill-novel` (purple). The novel sub-element — typically a cross-corpus join or a specific lobbyist's institutional history — should be called out explicitly in a "Novelty" paragraph at the top of the finding body. Prize panels read the novelty framing first; mislabeling a NYT-reported timeline as "novel" is a credibility hit.

---

## Template

`references/report-template.html` is a working skeleton with:
- Full CSS variables + classes (.pill-*, .path, .sources, .stats, .flag, .timeline, .phase, .pull, .deck, .tldr)
- A `<head>` block with the typography stack (Inter / EB Garamond / JetBrains Mono via system fallbacks)
- Header, TL;DR table, finding-stub, methodology stub (8 phase blocks), open-targets stub, footer
- HTML comments marking the agent customization points

Copy it, fill it, ship it.
