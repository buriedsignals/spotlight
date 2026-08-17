# Third-party notices

## claude-skills-journalism (MIT)

The `content-access` and `social-media-intelligence` skills are adapted from
[claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism)
by **Joe Amditis** (Center for Cooperative Media, Montclair State University),
released under the MIT License.

The `web-archiving` skill was originally adapted from the same collection but has
been fully rewritten (no upstream text remains); its methodology is inspired by
[OpenSanctions](https://www.opensanctions.org/) evidence-preservation practice.

## CTI Expert (MIT with Ethical Use Addendum)

The `technical-investigation` skill, coverage-state discipline in `investigate`,
contradiction categories in `epistemic-grounding`, public-ledger tracing in
`follow-the-money`, and verified technical-indicator exporter adapt reviewed
methods from [CTI Expert](https://github.com/7onez/cti-expert) by **Hieu Ngo /
chongluadao.vn** (GitHub: **7onez**).

The reviewed source revision is
`f9ecc9b0258caff78d26c0b779d1687f4431749f`. Spotlight does not redistribute or
execute CTI Expert's runtime. It ships source-mapped rewrites with high-risk and
out-of-scope techniques removed. The upstream MIT license is preserved at
`third_party/cti-expert/LICENSE`; revision and adaptation metadata live under
`third_party/cti-expert/`, `upstreams/cti-expert/`, and
`skills/technical-investigation/references/source-map.json`.

## Methodology attributions

See `LICENSE` for methodology attributions to Bellingcat, GIJN, Jim Shultz,
OCCRP/ICIJ, and others.

## diagram-design (MIT)

Spotlight's report-diagram guidance adapts visual principles from
[diagram-design](https://github.com/cathrynlavery/diagram-design) by **Cathryn
Lavery**, reviewed at revision
`09df49d8d1a1c7fb2efdfcdc7a2a0713534350a6`. Spotlight does not install,
execute, or redistribute the upstream plugin. It uses Mermaid/ELK and a small
local reference for report diagrams.
