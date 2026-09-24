<div align="center">

# Spotlight

### OSINT investigation orchestrator for AI agents

**Turns a lead into a structured case file — scoped brief, approved methodology, sourced findings, independent fact-checking, and provenance records. 17 skills, 7 runtimes, local-capable.**

[Install](#install) | [Workflow](#investigation-workflow) | [Integrations](#integrations) | [Runtimes](#runtimes) | [Website](https://spotlight.buriedsignals.com/)

[![License: MIT](https://img.shields.io/badge/license-MIT-00c853?style=for-the-badge&logo=opensourceinitiative&logoColor=white)](LICENSE)[![25 Skills](https://img.shields.io/badge/skills-25-0080ff?style=for-the-badge&logo=bookstack&logoColor=white)](https://github.com/buriedsignals/spotlight/tree/main/skills)[![7 Runtimes](https://img.shields.io/badge/runtimes-7-aa00ff?style=for-the-badge&logo=windowsterminal&logoColor=white)](#runtimes)[![Sovereign](https://img.shields.io/badge/sovereign_mode-SearXNG_+_Crawl4AI_+_local_models-00bfa5?style=for-the-badge&logo=shield&logoColor=white)](#source-acquisition)

[![Stars](https://img.shields.io/github/stars/buriedsignals/spotlight?style=flat-square&logo=github&label=Stars)](https://github.com/buriedsignals/spotlight/stargazers)[![Issues](https://img.shields.io/github/issues/buriedsignals/spotlight?style=flat-square&logo=github&label=Issues)](https://github.com/buriedsignals/spotlight/issues)[![Last Commit](https://img.shields.io/github/last-commit/buriedsignals/spotlight?style=flat-square&logo=github&label=Last%20Commit)](https://github.com/buriedsignals/spotlight/commits)[![Contributors](https://img.shields.io/github/contributors/buriedsignals/spotlight?style=flat-square&logo=github&label=Contributors)](https://github.com/buriedsignals/spotlight/graphs/contributors)

Built by [**Buried Signals**](https://buriedsignals.com/) • [tom@buriedsignals.com](mailto:tom@buriedsignals.com)

</div>

---

Spotlight turns an investigation lead into a structured case file: scoped brief,
approved methodology, sourced findings, independent fact-checking, review
artifacts, provenance records, and handoff-ready knowledge.

It is built for active OSINT casework. Give it a lead, URL, document, entity, or
question; it creates a working case directory, gathers source material, tests
claims against evidence, and stops at editorial gates instead of quietly
treating unfinished leads as publishable facts. The active case workspace is
separate from the knowledge vault: Spotlight queries the vault for prior context
when a case starts, and ingests verified material into the vault only after an
explicit end-of-case decision.

## What Spotlight Does

- Builds an investigation brief with the journalist before research starts.
- Drafts a methodology and waits for approval before execution.
- Runs bounded research cycles against public sources, local case files, and the
  journalist's vault.
- Saves source material into the case directory before citing it.
- Produces structured findings with source URLs, local files, confidence,
  evidence grounding, limitations, and monitoring recommendations.
- Runs an independent fact-check pass using SIFT-style source verification.
- Enforces readiness criteria before Gate 1 review.
- Produces review, summary, evidence, and provenance artifacts that can be
  inspected, exported, or ingested into a knowledge vault.

## Investigation Workflow

```text
Preflight
  -> Brief
  -> Methodology
  -> Research and fact-check cycles
  -> Gate 1 review
  -> Report decision
  -> Ingest, monitor, export, or continue
```

Every gate is explicit. Spotlight does not auto-advance through brief approval,
methodology approval, Gate 1 review, the report decision, or vault ingestion.

The research loop runs up to five cycles by default:

1. The investigator follows the approved methodology and writes findings.
2. The fact-checker independently checks the findings and writes verdicts.
3. The orchestrator checks source grounding, disputes, gaps, and readiness.
4. If the case is not ready, the next cycle targets the specific gaps.
5. If the case stalls, Spotlight asks the journalist whether to continue, pivot,
   or review the current material as-is.

See [docs/investigating.md](docs/investigating.md) for the full phase and gate
contract.

## Readiness Criteria

Spotlight only opens Gate 1 when the case has passed six editorial checks:

- Enough high-confidence findings.
- Independent source support for key claims.
- No unresolved disputed claims without a resolution path.
- At least one affected or non-official perspective when relevant.
- A document trail with primary sources, not only news coverage.
- Known gaps either resolved or stated as limitations.

These checks are not a truth guarantee. They are a forcing function that keeps
the case file honest about what is known, what is unsupported, and what still
needs reporting.

## Case Outputs

Each investigation gets an isolated working directory under the configured
`SPOTLIGHT_CASES_ROOT` active case workspace:

The cases root is trusted journalist-owned local storage. Active case directories must not be moved or replaced during an operation.

```text
{CASE_DIR}/
├── brief-directions.txt
├── summary.md
├── review.html
├── report.html
├── findings-report.md
├── evidence-map.json
├── data/
  │   ├── methodology.json
  │   ├── findings.json
  │   ├── fact-check.json
│   ├── source-expressions.json  # opt-in pilot or activated cases
│   ├── case-contract.json       # activated 1.1 cases only
│   ├── evidence-bundle.json
│   ├── investigation-log.json
│   ├── summary.json
│   ├── provenance-manifest.json
│   ├── knowledge-batch.json      # optional reviewed claim/event/story promotion
│   └── monitoring.json
├── research/
│   ├── *.md
│   ├── *.json
│   └── archived/
└── evidence/
    ├── *.png
    └── *.pdf
```

The JSON files validate against schemas in [schemas/](schemas/). The markdown
and HTML files are for human review. The evidence and research folders preserve
the local trail behind the claims.

### Source-expression pilot

Spotlight can preserve an exact source passage as a first-class
`SourceExpression`: original wording, case-local locator, hashes, attribution,
language, lifecycle, and explicit links to one or more findings. This layer
does not replace findings or fact-check verdicts.

The pilot-capable release has three distinct states:

- **Legacy (default):** new cases emit findings contract `1.0` and do not
  produce source expressions.
- **Pilot (explicit opt-in):** producers write `data/source-expressions.json`
  as a side artifact while the case remains legacy `1.0`. File presence does
  not activate the case.
- **Activated:** findings contract `1.1` and a valid append-only
  `data/case-contract.json` are both present. The receipt is the sole activation
  authority; activated cases cannot silently fall back to legacy handling.

The checks are deliberately bounded. They can prove that selected text,
locators, hashes, references, lifecycle state, and rendered quotations agree.
They cannot prove that a passage is true, entails a finding, captures all
relevant context, or is framed fairly. Independent fact-checking and human
editorial review still own those judgments.

The comparative pilot record is
[`docs/source-expression-pilot-results.json`](docs/source-expression-pilot-results.json).
Human correction yield and review time have not yet been measured, so strict
activation is **NOT APPROVED** and default new-case emission remains `1.0`.
Recovery, migration, and mode-selection instructions are in
[`docs/investigating.md`](docs/investigating.md#source-expression-mode).

### Reviewed claim–event–story graph

Approved knowledge is committed to a local SQLite graph whose versioned
relation records support deterministic traversal in both directions:

```text
source expression ↔ claim ↔ event ↔ story arc
```

Broad discovery stays in the journalist's local Open Knowledge project. A
Spotlight-local writer creates one managed investigation block and one
case-scoped page per linked story, journals changes, and attaches receipts; it does not create a Markdown
file per claim or event. Open Knowledge owns indexing and reuses its installed
BGE-M3 embeddings. Exact claim, event, deduplication, and prior-verdict queries
read the SQLite graph directly. Engine remains responsible for installation,
updates, configuration, and doctor, not the runtime knowledge path.

Local activation can retire new standalone claim notes after the graph,
projection, receipt-aware discovery, logical-removal, and migrated-workflow
checks pass. Existing claim notes remain untouched as legacy records.
The supported boundary is a same-user local workspace; multi-user newsroom
authorization is outside this release. See
[`docs/knowledge-destination.md`](docs/knowledge-destination.md).

## Source Acquisition

Spotlight's default source path is simple:

1. Scrape locally with Crawl4AI and search through the configured local search seam.
2. Save the source artifact locally.
3. Archive or capture the source when preservation matters.
4. Cite only material that can be traced back to a local file.

Use `dev-browser` only for specific investigative tasks that require browser
automation: dynamic pages, search forms, authenticated portals, rendered tables,
downloads, visual evidence, or multi-step UI navigation. Firecrawl is an
optional hosted fallback for pages the local scraper cannot reach.

## Integrations

Spotlight is runtime-agnostic, but investigations need specialized tools. The
important integrations are:

| Integration | Purpose |
|---|---|
| Crawl4AI | Default local scraper for public web sources. |
| Firecrawl | Optional hosted fallback for difficult pages. |
| dev-browser | Interactive or headless browser acquisition with screenshots, HTML, metadata, hashes, and journalist-controlled authentication. |
| OSINT Navigator | Tool discovery and method routing when the built-in catalog is not enough. |
| Mycroft | Passive signals, vault memory, and the optional handoff into durable monitoring. |
| Junkipedia | Narrative and misinformation tracking when the newsroom has access. |
| Apify | Optional hosted social-media collection (X, Instagram, TikTok, Facebook, LinkedIn) when `APIFY_API_TOKEN` is set. Collection on X violates the platform ToS even for public posts; record the collection authority per capture. |
| Unpaywall | Legal open-access lookup for academic papers. |
| Noosphere C2PA | Optional provenance signing for case-level packages. |

See [docs/integrations.md](docs/integrations.md) for setup and routing details.

## Install

Two routes, one decision:

- **Engine (managed).** Buried Signals Engine (`bsig`) installs a
  catalog-pinned, signed release, places skills only for the selected local
  runtime in its actual project checkout, holds integration credentials in the
  OS keychain, and updates through reviewed plans. This is the managed route
  for journalist installs; the native-ownership cutover's full acceptance is
  still blocked (see [Runtimes](#runtimes)).
- **Source (lighter).** A sparse clone plus per-skill links, described under
  [Install from source](#install-from-source-agents). Skills load, scripts run,
  nothing else: no credentials, no updates, no repair. Choose this only when an
  agent or developer is working from a clone and will re-link after every pull.

**Buried Signals Engine (`bsig`) is Spotlight's only managed installation authority.**
Indicator Labs submits the same Engine plans while adding guided runtime/model
selection, credential prompts, repair, and automatic updates for
[members](https://buriedsignals.com/join).

For agent-led manual setup, fetch the signed Engine descriptor at:

```text
https://navigator.indicator.media/api/artifacts/bootstrap/bsig/<platform>
```

Use `darwin-arm64`, `darwin-amd64`, `linux-arm64`, `linux-amd64`, or
`windows-amd64`. Download the archive, checksum, signature, and public key
before the 60-second grants expire; verify SHA-256 and Minisign before running
`bsig`. Then:

```text
bsig catalog sync
bsig --json configure describe spotlight
```

The agent selects values from that signed descriptor and submits the JSON
request on stdin to `bsig configure plan spotlight`. Review and apply the
emitted `plan.json`. Preflight and `bsig keys list` name optional integration
credential IDs. A trusted local agent may prepare Engine's protected
stdin/keychain flow, but the user enters each value only through a private
operating-system or terminal prompt—never argv, chat, shell history, or a
repository file. Manual updates use `bsig plan update spotlight`; Indicator
Labs automates the same lifecycle.

[`install-spotlight.sh`](install-spotlight.sh) remains a fail-closed pointer for
old `curl | bash` commands; it is not another installer. Contributors may clone
[`buriedsignals/spotlight`](https://github.com/buriedsignals/spotlight), but
Engine installs the catalog-pinned public commit for journalists. Runtime
development details remain in [docs/runtimes.md](docs/runtimes.md).

### Managed skill ownership

Spotlight's verified bundles and ownership ledger are private to Engine:
`<Engine base>/skills/spotlight/bundles/` and
`<Engine base>/skills/spotlight/registry.json`. Runtime discovery uses flat
`<skill-id>/SKILL.md` leaves under the selected checkout's native skills root,
not a home-global `spotlight/` namespace or fan-out to every detected runtime.
See the [runtime placement table](docs/runtimes.md#project-local-placement-and-execution-host).
Windows and Antigravity use verified copies; other supported local routes use
symlinks. Mycroft retains its separate `~/.ok/skills` store and shared ledger.

The installed manifest seals the runtime and `native_skill_ids`, including
Navigator opt-out. When enabled, Navigator's authority is the catalog-pinned
installed `navigator-cli` distribution and verified skill bytes, never a
similarly named source checkout. OpenKnowledge remains the local knowledge
service/index dependency; it is not Spotlight skill storage.

Managed launch requires active, committed ownership. Migration and recovery
bind the reviewed plan hash to a unique apply commit identity and check retained
recovery receipts. Unknown or changed foreign files are refused and retained;
Engine does not blindly adopt source links, overwrite them, or roll back the
whole shared ledger. Inspect the blocker and retained evidence before a reviewed
update. Do not hand-edit ledgers, fabricate receipts, or blindly retry apply.

## Install from source (agents)

Engine is the supported path above. An agent pointed at this repository can
install the runtime set without cloning the whole repository: `install-set.txt`
names the directories a frontier runtime needs; everything else is documentation,
example cases, the local-model harness, and development tooling. Scripts use the Python 3 standard library only.

```bash
git clone --filter=blob:none --sparse https://github.com/buriedsignals/spotlight.git spotlight
cd spotlight
git sparse-checkout set $(grep -v '^#' install-set.txt)
```

Skills and `scripts/` stay together in this checkout: every script path inside
a skill is relative to the checkout root, and the agent runs Spotlight from a
shell whose working directory is that root (see `AGENTS.md`, "Checkout
layout").

For an **unmanaged development checkout**, choose just the native project root
for the runtime you will actually launch:

| Local runtime | Skills directory relative to checkout |
|---|---|
| Pi / Flue, Codex CLI, ChatGPT Desktop Codex Local, Goose | `.agents/skills/` |
| Claude Code / Claude Desktop Code Local | `.claude/skills/` |
| Cursor | `.cursor/skills/` |
| Gemini CLI | `.gemini/skills/` |
| OpenCode | `.opencode/skills/` |
| Antigravity | `.agents/skills/` (copies, not symlinks) |

Create one flat leaf per skill directory in that selected directory, each
containing `SKILL.md`; never link the repository root or add another
`spotlight/` namespace around the leaves. On macOS/Linux, a leaf may be a
symlink to its absolute `skills/<id>` source path. For Windows or Antigravity,
copy each complete skill directory and verify its contents instead. Refuse
existing destinations rather than force-replacing them. Keep these unmanaged
leaves in a dedicated development checkout, away from an Engine-owned install.
Gemini also needs project instructions in `GEMINI.md` (the same contract as
`AGENTS.md`). This setup provides discovery, not managed ownership or credentials.

Start the selected runtime with this exact checkout as its working project and
shell cwd. A different workspace, detached worktree, or remote/cloud app without
access to these local files is not covered. Claude Desktop must use Code → Local;
ChatGPT Desktop must use Codex → Local, not ordinary Chat or Cloud.

Some runtimes rebuild their skill listing at turn boundaries rather than on
filesystem change, so freshly linked skills become visible on the agent's next
turn. An immediate "Unknown skill: spotlight" in the installing turn is not
evidence of a failed install; wait for the next turn before troubleshooting.

These source links/copies remain unmanaged. An Engine install must establish
its own verified ownership through a reviewed plan; it does not automatically
adopt them. Never hand-build an ownership ledger or receipt to bypass a conflict.

## Runtimes

Two shapes, one source of truth (the skills + `agents/*.md` role files):

- **Frontier runtimes** (Claude Code, Codex CLI, Pi): the runtime *is* the
  harness — it loads the skills natively, gates are chat turns, nothing else installs.
- **Non-frontier** (local GGUFs and API providers like Fireworks/OpenRouter): the
  repo's **Flue-on-Pi harness** (`harness/flue/`) provides orchestration — native
  investigator/fact-checker subagents, durable resumable sessions, RLM distillation,
  and conversation compaction. Engine launches from the actual checkout with an
  explicit `harness/flue/flue.config.ts`, not from the harness subdirectory, and
  passes scoped dependency paths and the sandbox's explicit environment.

Per-runtime wiring lives in [docs/runtimes.md](docs/runtimes.md). The
machine-readable contract lives in [AGENTS.md](AGENTS.md).

Goose remains a supported local workflow route; native discovery does not
require rewriting the workflow skills. Local evidence for this cutover includes
Goose 1.50.0 discovering 25 intended skills in an isolated checkout, Flue SDK
discovery and actual sandbox cwd/dependency/environment checks, mixed-ownership
migration/update/uninstall preserving Mycroft and synthetic case/knowledge/graph
bytes, and missing-browser repair followed by an actual Crawl4AI raw-HTML task.
These are bounded checks, not a completed model investigation, native Windows
acceptance, or packaged Desktop acceptance. A Desktop handoff is not runtime
health, and the original timeout cause is not established. Full acceptance
remains blocked.

## Local Models

Local model selection is an implementation detail, not the product. Spotlight
can use cloud, ZDR, or local inference depending on the runtime and newsroom
policy. On the managed local tier, use `bsig spotlight` (or the installed
`spotlight` launcher); Engine binds the selected runtime, checkout, active case,
model configuration, and credentials. Changing managed configuration requires
the Engine configuration/plan flow, not a hand-edited `.env`. Unmanaged harness
development and model-tier details are in [docs/runtimes.md](docs/runtimes.md).

## Documentation

| Doc | For |
|---|---|
| [docs/README.md](docs/README.md) | Operator manual entry point. |
| [docs/investigating.md](docs/investigating.md) | Pipeline phases, gates, cycles, readiness, and stall protocol. |
| [docs/fact-checking.md](docs/fact-checking.md) | Independent verification, SIFT, verdict taxonomy, and evidence trails. |
| [docs/epistemic-grounding.md](docs/epistemic-grounding.md) | Claim-to-evidence grounding and confidence caps. |
| [docs/monitoring.md](docs/monitoring.md) | Monitoring recommendations and explicit Mycroft handoff. |
| [docs/structure.md](docs/structure.md) | Repo layout, schemas, skills, agents, and extension points. |
| [docs/runtimes.md](docs/runtimes.md) | Runtime wiring. |
| [docs/integrations.md](docs/integrations.md) | External tools and preflight. |
| [AGENTS.md](AGENTS.md) | Runtime contract loaded by agents. |

## What Belongs Where

- **Spotlight** is active OSINT casework: briefs, evidence, captures, findings,
  fact-checks, review artifacts, exports, and handoffs.
- **Mycroft** is durable newsroom memory and publishing support: source records,
  wiki notes, recurring briefings, draft checks, story material, and Spotlight
  handoffs.
- **Scoutpost** is an optional Mycroft-owned monitoring integration. A
  standalone Spotlight install never configures or invokes it.

## Attribution

- Content Access and Social Media Intelligence skills adapt work from
  [jamditis/claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism)
  by Joe Amditis (Center for Cooperative Media). Web Archiving, originally adapted
  from the same collection, is now a full rewrite whose methodology is inspired by
  OpenSanctions evidence-preservation practice.
- Follow the Money synthesizes public investigative-finance methodology from
  Jim Shultz, GIJN, EBU, and related training material.
- Investigate includes methodology influenced by Bellingcat training material.
- Technical Investigation, investigation-coverage discipline, contradiction
  categories, public-ledger tracing, and verified technical-indicator export
  adapt reviewed methods from [CTI Expert](https://github.com/7onez/cti-expert)
  by Hieu Ngo / chongluadao.vn (GitHub: 7onez), reviewed at
  [`f9ecc9b`](https://github.com/7onez/cti-expert/tree/f9ecc9b0258caff78d26c0b779d1687f4431749f)
  under the MIT License with the upstream Ethical Use Addendum. Spotlight ships
  source-mapped rewrites, not CTI Expert's runtime, installers, active techniques,
  or scoring system. [Implementation details](docs/technical-investigation.md).
  A daily read-only watcher records upstream drift in a review issue; online
  Spotlight installs and updates receive the latest reviewed adaptation, while
  offline installs remain reproducibly pinned.

## Acknowledgements

Spotlight stands on open work — community-maintained open-source projects and
open methods. A sincere thank-you to every project below — Spotlight would not
exist without them. *(Listing does not imply affiliation or endorsement.)*

| Category | Projects we're grateful to |
|----------|----------------------------|
| **Loop harness** | [pi](https://pi.dev/) (Mario Zechner, MIT — the loop-harness coding agent) · [Flue](https://github.com/withastro/flue) (Apache-2.0 — the harness runtime) |
| **Sovereign search & scraping** | [SearXNG](https://github.com/searxng/searxng) (AGPL-3.0) · [Crawl4AI](https://github.com/unclecode/crawl4ai) (unclecode, Apache-2.0) · [Playwright](https://playwright.dev/) (browser automation) · [Poppler](https://poppler.freedesktop.org/) (`pdftotext` — PDF extraction) · [Tor](https://www.torproject.org/) (opt-in anonymous fetching) |
| **Local inference** | [llama.cpp](https://github.com/ggml-org/llama.cpp) (ggml, MIT) · [Unsloth](https://unsloth.ai/) (GGUF quants of the local operator model) |
| **OSINT tooling** | [Maigret](https://github.com/soxoj/maigret) (soxoj, MIT) · [Sherlock](https://github.com/sherlock-project/sherlock) (MIT) · [Holehe](https://github.com/megadose/holehe) (GPL-3.0) · [ExifTool](https://exiftool.org/) (Phil Harvey) · [Unpaywall](https://unpaywall.org/) (OurResearch — open-access paper lookup) |
| **Evidence & provenance** | [Internet Archive / Wayback Machine](https://web.archive.org/) · [Archive.today](https://archive.today/) · [C2PA](https://c2pa.org/) (content-provenance standard for case packages) · [OpenSanctions](https://www.opensanctions.org/) (evidence-preservation methodology) |
| **Knowledge workspace** | [OpenKnowledge](https://github.com/inkeep/open-knowledge) (local case knowledge, search, and runtime-aware agent chat) |
| **Reports & site** | [Mermaid](https://github.com/mermaid-js/mermaid) (MIT — report diagrams) · [diagram-design](https://github.com/cathrynlavery/diagram-design) by Cathryn Lavery (MIT — visual principles adapted for report diagrams; reviewed at `09df49d`) · [Three.js](https://github.com/mrdoob/three.js) (mrdoob, MIT — the landing scene) |
| **Methodology** | [CTI Expert](https://github.com/7onez/cti-expert) by Hieu Ngo / chongluadao.vn (GitHub: 7onez; selected methods substantially adapted at `f9ecc9b`; MIT + upstream Ethical Use Addendum) · [Joe Amditis](https://github.com/jamditis/claude-skills-journalism) (MIT) · [SIFT — Mike Caulfield](https://hapgood.us/2019/06/19/sift-the-four-moves/) · [Bellingcat](https://www.bellingcat.com/) · [GIJN](https://gijn.org/) (Patrucic & Cosic, CC BY-ND) · Jim Shultz — Follow the Money · [Derek Bowler · EBU](https://www.ebu.ch/) |

> Built something here we should credit, or want a listing changed or removed?
> Open an issue or PR — we'll fix it fast.

## License

See upstream plugin licenses. Spotlight additions by Buried Signals.
