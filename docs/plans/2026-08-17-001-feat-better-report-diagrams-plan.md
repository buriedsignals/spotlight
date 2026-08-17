---
title: "feat: Improve Spotlight report diagrams with diagram-design"
type: feat
status: active
date: 2026-08-17
---

# feat: Improve Spotlight report diagrams with diagram-design

## Goal

Use [`cathrynlavery/diagram-design`](https://github.com/cathrynlavery/diagram-design) as a pinned design reference so Spotlight generates clearer diagrams in its existing reports.

The upstream project is not installed or called at runtime. Spotlight keeps its current findings, report draft, validator, finalizer, Mermaid/ELK stack, report styles, and output files.

## Current gap

Spotlight already records directed relationships in `data/findings.json`, builds reports from `data/report-draft.json`, and has a working Mermaid/ELK recipe in `skills/report-drafting/references/interactive-diagrams.md`.

The deterministic renderer in `scripts/render-report.py` does not yet consume diagram choices or emit that Mermaid markup. This change adds that missing path inside the existing report pipeline. It does not add another pipeline stage.

## Scope

The first change supports four diagram types aimed at the money and relationship structures Spotlight reports already describe:

| Type | Use |
|---|---|
| `flow` | Money, assets, information, or influence moving between entities. |
| `hierarchy` | Ownership, control, parent/subsidiary, or command structures. |
| `network` | Directed material relationships without one hierarchy or main path. |
| `loop` | A directed cycle, such as money returning to an earlier entity. |

Each type is one small validator/compiler branch over the same connection data. Adding a later type should require another enum entry, reference entry, and compiler branch, not another subsystem.

Out of scope:

- Importing the upstream plugin, templates, fonts, icons, profiles, motion, or export tools.
- Creating another graph, evidence, approval, or report artifact.
- Requiring any node-by-node interaction.
- Changing any part of Spotlight outside report-diagram selection and rendering.
- Adding a Splash chart type.

## Report-draft contract

Add an optional `diagrams` array to the existing `report-draft.json` contract. Reports without it remain unchanged. Each item contains:

| Field | Value |
|---|---|
| `id` | Unique report-local ID. |
| `type` | `flow`, `hierarchy`, `network`, or `loop`. |
| `title` | Short figure title. |
| `caption` | One or two sentences explaining the figure. This is also the accessible description. |
| `finding_ids` | Existing findings that support the figure. |
| `connections` | Exact `{from, to, relationship}` selectors for existing `findings.json.connections`. |
| `focal_entities` | Optional selected endpoint labels; at most two. A `loop` requires exactly one, which is its visual starting entity. |

The renderer derives every node and edge from the selected `findings.json.connections`. The figure-level finding IDs provide the same structural support link used by other report-draft synthesis; diagrams do not change finding or fact-check status. The draft cannot contain Mermaid, SVG, HTML, JavaScript, CSS, coordinates, URLs, or theme settings.

Validation must:

- resolve every selector to exactly one current connection;
- reject unknown finding IDs, endpoints, types, duplicate selectors, and ambiguous selectors;
- limit a diagram to 9 nodes, 12 connections, and 2 focal entities;
- preserve each selected `from` → `to` direction for every type; and
- reject cycles for `hierarchy`; require a `loop` to be one simple directed cycle containing its focal entity and every selected connection; and allow `flow` and `network` to contain cycles without changing the recorded edge directions.

Keep the current report-draft schema version. This is an optional field handled by the report skill, validator, and renderer shipped together.

## Rendering rules

Adapt these rules from the pinned upstream revision:

- one primary diagram type and one clear reading direction;
- low density, with no more than two focal nodes;
- orthogonal connectors where the selected Mermaid layout supports them;
- short edge labels and visibly different structural roles where the topology provides them;
- neutral structural styling, with existing verdict colors left to their current meanings;
- Spotlight typography and colors, no upstream fonts or palette, and no shadows; and
- a visible legend outside the drawing area.

Compile types as follows:

| Type | Mermaid treatment |
|---|---|
| `flow` | Left-to-right flowchart using ELK. |
| `hierarchy` | Top-to-bottom flowchart using ELK; style roots, branches, and leaves. |
| `network` | Deterministically oriented flowchart using ELK; preserve all edge directions. |
| `loop` | Follow the cycle from the focal entity, declare it first, and use the existing dagre treatment with the unique edge returning to the focal entity styled as the return path. |

Use the figure title as Mermaid's accessible title and the caption as its accessible description. Render diagrams as one section after the findings summary, with links to the referenced finding sections.

Reuse the font-ready initialization, overflow fixes, and canvas controls already documented in `interactive-diagrams.md`. Pin the imports to `https://cdn.jsdelivr.net/npm/mermaid@11.16.1/dist/mermaid.esm.min.mjs` and `https://cdn.jsdelivr.net/npm/@mermaid-js/layout-elk@0.2.2/dist/mermaid-layout-elk.esm.min.mjs`; do not use floating tags. Include them only when `diagrams` is non-empty, emit no Mermaid `click` directives, and use strict security mode. Set initial zoom from node count: `1.0` for up to 6 nodes and `0.85` for 7–9 nodes. The figure title, caption, referenced findings, and selected relationship labels remain visible if the browser cannot render Mermaid.

## Implementation changes

1. Add `skills/report-drafting/references/diagram-design.md` with the four type choices and adapted design rules. Link it from `skills/report-drafting/SKILL.md` and `interactive-diagrams.md`. Record the pinned commit and MIT attribution in `NOTICE.md`.
2. Extend `schemas/report-draft.schema.json` and `scripts/validate-report-draft.py` with the optional contract and validation above.
3. Extend `scripts/render-report.py` and `skills/report-drafting/references/report-template.html` with the type compiler, figure markup, conditional Mermaid/ELK runtime, and Spotlight diagram styles.
4. Add focused diagram cases to `tests/render-report-check.py` and a new `tests/report-diagrams-check.py`; regenerate `plugins/spotlight/` with the existing payload builder.

## Acceptance criteria

- A fixture for each of the four types renders from existing `findings.json.connections` in both `report.html` and `findings-report.md`.
- Identical inputs produce identical report bytes and Mermaid source.
- Labels containing quotes, newlines, backticks, Mermaid keywords, HTML, or script-like text remain inert.
- Invalid selectors, unsupported types, over-budget diagrams, invalid hierarchies, and loops that are non-cyclic, branched, disjoint, or contain multiple cycles fail before outputs are replaced.
- Reports without `diagrams` retain their current output and contain no Mermaid runtime.
- Browser-rendered wide and narrow fixtures for all four types show the intended reading direction, unclipped labels, distinct connectors, a legible legend, and clear focal emphasis. At least one money-structure fixture preserves the topology and label lengths of an existing Spotlight diagram and is compared with the current generic treatment.
- Diagram reports use only the two exact pinned runtime URLs; reports without diagrams include neither.
- Root files and the generated plugin payload pass the existing distribution-parity check.
- No new case file, pipeline stage, or manual review step is introduced.

## References

- Local path: `schemas/findings.schema.json`, `schemas/report-draft.schema.json`, `scripts/validate-report-draft.py`, `scripts/render-report.py`, `skills/report-drafting/SKILL.md`, and `skills/report-drafting/references/interactive-diagrams.md`.
- Upstream revision: [`diagram-design` at `09df49d`](https://github.com/cathrynlavery/diagram-design/commit/09df49d8d1a1c7fb2efdfcdc7a2a0713534350a6), including its [Architecture](https://github.com/cathrynlavery/diagram-design/blob/09df49d8d1a1c7fb2efdfcdc7a2a0713534350a6/skills/diagram-design/references/type-architecture.md), [Tree](https://github.com/cathrynlavery/diagram-design/blob/09df49d8d1a1c7fb2efdfcdc7a2a0713534350a6/skills/diagram-design/references/type-tree.md), [Loop](https://github.com/cathrynlavery/diagram-design/blob/09df49d8d1a1c7fb2efdfcdc7a2a0713534350a6/skills/diagram-design/references/type-loop.md), and [MIT license](https://github.com/cathrynlavery/diagram-design/blob/09df49d8d1a1c7fb2efdfcdc7a2a0713534350a6/LICENSE).
- Mermaid layout behavior: [official ELK layout documentation](https://github.com/mermaid-js/mermaid/blob/develop/packages/mermaid-layout-elk/README.md), checked through `ctx7` for `/mermaid-js/mermaid` on 2026-08-17.
- Runtime versions: [Mermaid 11.16.1](https://www.npmjs.com/package/mermaid/v/11.16.1) and [`@mermaid-js/layout-elk` 0.2.2](https://www.npmjs.com/package/@mermaid-js/layout-elk/v/0.2.2), checked from the npm registry on 2026-08-17.
