# Required structure per finding (HTML)

Every `<section class="finding">` MUST contain, in order:

1. **Header row** — `<h2>` with embedded finding ID + `.pill-novel` (purple, genuinely new evidence) OR `.pill-connected` (outline, new framing of public facts), plus `.pill-high` / `.pill-med` / `.pill-low` for confidence.
2. **Deck** — one-line subhed under the H2 in `<p class="deck">` (≤60ch).
3. **Stats grid** (optional) — `<div class="stats">` for findings with a quantitative spine.
4. **Body paragraphs** — `<p>` (auto-constrained to ≤72ch via column width). Quote primary-source text via `Read` of the archived page, never paraphrase from memory.
5. **`<div class="path" aria-label="How we got here">`** — REPLICATION PATH. One `.step` + `.what` pair per phase that produced this finding. Cite SQL hashes (from `anomalies/*/provenance.json`), script paths, archived URLs (from the finding's citation manifest only). This block makes the finding auditable in under a minute. **Mandatory.**
6. **`<div class="sources">`** — primary-source URLs (not secondary commentary) with archive references, all from the citation manifest. **Mandatory.**

Optional add-ins:
- `<div class="flag">` for legal qualifications — use `<span class="flag-label">` for the in-line label, NOT `<strong style="display:block">`.
- `<div class="timeline">` for chronological evidence chains (4-column grid: date, event, source).
- `<div class="pull">` for a 1–2 sentence pull quote inside the finding body.

**Novelty labelling:** if the core claim was already published by a mainstream outlet (NYT/ProPublica/WaPo/Reuters…), it gets `.pill-connected` (outline), NOT `.pill-novel`. Call out the genuinely novel sub-element (a cross-corpus join, a specific institutional history) in a "Novelty" paragraph at the top of the finding body — panels read the novelty framing first; mislabeling a reported timeline as "novel" is a credibility hit.
