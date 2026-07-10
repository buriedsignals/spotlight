# Design discipline

CSS variables already in the template (`--ink`, `--paper`, `--rule`, `--bg-soft`, `--red`, `--mono`, `--sans`, `--serif`). Do not reinvent them per finding.

**Max-width rules** (the template enforces these; do not override per-element):
- `h1` → 28ch
- `h2` → 32ch
- `.deck` (subhed) → 60ch
- Body `<p>` → constrained by the column, `max-width:none`
- `.lede` → constrained by the column, `max-width:none`
- Tables / `.stats` / `.path` / `.sources` / `.flag` / `.timeline` / `.phase` → full column width, no max-width

**Pill semantics:**
- `.pill-novel` (purple background) — genuinely new evidence not previously published.
- `.pill-connected` (outline) — new framing of public facts via cross-corpus join.
- `.pill-verified` (green) — fact-checker confirmed.
- `.pill-partial` (amber) — fact-checker partial verdict.
- `.pill-high` / `.pill-med` / `.pill-low` — confidence levels.
- `.pill-id` (mono, light) — finding ID badge.

**TL;DR table** at the top: one row per finding, `<a href="#c-NNN">` linked, with novelty + confidence pills inline.

**AI assistance notice:** use the template's `.honesty` block; include verbatim, do not soften the responsibility language.

The HTML is a *designed* document, not a markdown render — tables, grids, pill systems, two-column blocks are the point. If your HTML reads like `pandoc` output, restart from the template.
