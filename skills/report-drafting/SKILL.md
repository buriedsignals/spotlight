---
name: report-drafting
description: "Phase 5 synthesis sub-skill — draft the journalist-grade findings report: findings-report.md, report.html (from the report-template.html skeleton), and evidence-map.json, with per-finding replication paths + sources strip, confidence/novelty pills, phase-by-phase methodology, and interactive mermaid network/money-flow diagrams. Use at Phase 5 after Gate 1, before the spotlight handoff. Triggers on draft the report, build the HTML report, findings report, evidence map, synthesis phase, journalist-grade output, network diagram, money-flow diagram."
license: MIT
metadata:
  type: orchestration-subskill
  parent: data-detective
  phase: 5
invocable_by: [orchestrator, user]
---

# report-drafting — Phase 5 synthesis

You are at Phase 5. Gate 1 has approved verified findings. Ship the three deliverables editors actually read, building the HTML **from the template, not from scratch**.

**This skill is a workflow plus a set of references you load ON DEMAND** — read each `references/*.md` only when you reach the step that needs it; do NOT read them all up front. That keeps your working context small during the most context-heavy phase.

## Deliverables (all three required)

| File | Audience | What it is |
|---|---|---|
| `case/findings-report.md` | editor / fact-checker | narrative audit, one section per finding — the authoritative claim-by-claim record |
| `case/report.html` | publication / reader | designed journalism artifact, built from `references/report-template.html` |
| `case/evidence-map.json` | audit / replication | machine-readable ledger: claim → cards → query hashes → URLs (see data-detective `references/evidence-map-format.md`) |

## Mandatory AI-assistance notice (verbatim, in report.html)

Top of the page, in the template's `.honesty` block, after the byline and before the TL;DR — do not soften:
> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

## Workflow (open the referenced file when you reach that step)

1. Copy `references/report-template.html` → `case/report.html`. Fill header (title, deck, byline, lede) + the AI notice + the TL;DR table from `findings.json`.
2. **Before any finding, build its citation manifest and obey the citation rule** → read **`references/citation-discipline.md`** (CRITICAL: the synthesis layer must NEVER originate a UUID/URL/quote — every one traces to a ground-truth file; write each finding's allowed set to `/tmp/c-NNN-citations.txt`).
3. For each verified finding: draft the `<section class="finding">` per **`references/finding-structure.md`** (header+pills, deck, body with quoted primary text, mandatory `.path` replication block + `.sources` strip). Style per **`references/design-discipline.md`** (CSS vars, max-widths, pill semantics — don't reinvent).
4. Methodology: one `<div class="phase">` per phase (P0–P7) → **`references/methodology-pattern.md`** (fact-check verdict table INSIDE Phase 3; spotlight-handoff table INSIDE Phase 6; strict phase order).
5. If the case has relational structure (**default** — networks / money-flows / funnels, ≥3 actors with directed relationships): build interactive diagram section(s) per **`references/interactive-diagrams.md`** (follow it exactly — the "simpler" version clips labels and pixelates). Skip only if genuinely no relational structure, and say so in the run log.
6. Write `findings-report.md` (narrative, no styling, every claim from the same allowed-set manifest) and `evidence-map.json`.
7. **Run the citation closure script** (in `references/citation-discipline.md`) — every UUID and URL in the three files must trace to ground-truth; fix orphans. Then validate + smoke-test per **`references/html-protocol.md`**.
8. Append `synthesis_complete` + `draft_paths` + `citation_closure_passed` to `investigation-log.json`.

**Whenever you edit `report.html`:** never greedy-regex it → **`references/html-protocol.md`** (anchored `Read`+`Edit` only; a greedy `re.sub` once destroyed a whole report). Review **`references/anti-patterns.md`** if unsure.

## Inputs / Outputs

**Reads:** `case/data/{findings,fact-check,investigation-log}.json`, `case/anomalies/*/provenance.json`, `case-trace/spotlight/results/*/`.
**Writes:** `case/findings-report.md`, `case/report.html`, `case/evidence-map.json`.

## References (load on demand — do not preload)

- `references/report-template.html` — the HTML skeleton (step 1)
- `references/citation-discipline.md` — the hard citation rule, manifest build, closure script (steps 2, 7) **← highest-stakes**
- `references/finding-structure.md` — per-finding HTML structure (step 3)
- `references/design-discipline.md` — CSS vars, max-widths, pill semantics (step 3)
- `references/methodology-pattern.md` — the phase-ordered methodology (step 4)
- `references/interactive-diagrams.md` — the full mermaid recipe + headless smoke test (step 5)
- `references/html-protocol.md` — safe HTML editing + validation (whenever editing report.html)
- `references/anti-patterns.md` — the learned failure modes
