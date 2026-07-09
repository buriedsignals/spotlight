<div align="center">

# Spotlight

### OSINT investigation orchestrator for AI agents

**Turns a lead into a structured case file — scoped brief, approved methodology, sourced findings, independent fact-checking, and provenance records. 16 skills, 7 runtimes, local-capable.**

[Install](#install) | [Workflow](#investigation-workflow) | [Integrations](#integrations) | [Runtimes](#runtimes) | [Website](https://spotlight.buriedsignals.com/)

[![License: MIT](https://img.shields.io/badge/license-MIT-00c853?style=for-the-badge&logo=opensourceinitiative&logoColor=white)](LICENSE)[![16 Skills](https://img.shields.io/badge/skills-16-0080ff?style=for-the-badge&logo=bookstack&logoColor=white)](https://github.com/buriedsignals/spotlight/tree/main/plugins/spotlight/skills)[![7 Runtimes](https://img.shields.io/badge/runtimes-7-aa00ff?style=for-the-badge&logo=windowsterminal&logoColor=white)](#runtimes)[![Sovereign](https://img.shields.io/badge/sovereign_mode-SearXNG_+_Crawl4AI_+_local_models-00bfa5?style=for-the-badge&logo=shield&logoColor=white)](#source-acquisition)

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
  -> Ingest, monitor, export, or continue
```

Every gate is explicit. Spotlight does not auto-advance through brief approval,
methodology approval, Gate 1 review, or vault ingestion.

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

```text
{CASE_DIR}/
├── brief-directions.txt
├── summary.md
├── review.html
├── data/
│   ├── methodology.json
│   ├── findings.json
│   ├── fact-check.json
│   ├── evidence-bundle.json
│   ├── investigation-log.json
│   ├── summary.json
│   ├── provenance-manifest.json
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

## Source Acquisition

Spotlight's default source path is simple:

1. Search and scrape with Firecrawl.
2. Save the source artifact locally.
3. Archive or capture the source when preservation matters.
4. Cite only material that can be traced back to a local file.

Use `dev-browser` only for specific investigative tasks that require browser
automation: dynamic pages, search forms, authenticated portals, rendered tables,
downloads, visual evidence, or multi-step UI navigation. Firecrawl remains the
first acquisition path for ordinary search and scrape work.

## Integrations

Spotlight is runtime-agnostic, but investigations need specialized tools. The
important integrations are:

| Integration | Purpose |
|---|---|
| Firecrawl | Search and scrape public web sources into local case files. |
| dev-browser | Interactive or headless browser acquisition with screenshots, HTML, metadata, hashes, and journalist-controlled authentication. |
| OSINT Navigator | Tool discovery and method routing when the built-in catalog is not enough. |
| Scoutpost | Durable monitoring for leads, sources, and follow-up developments. |
| Mycroft | Passive signals, vault memory, and handoff into durable newsroom knowledge. |
| Junkipedia | Narrative and misinformation tracking when the newsroom has access. |
| Unpaywall | Legal open-access lookup for academic papers. |
| Noosphere C2PA | Optional provenance signing for case-level packages. |

See [docs/integrations.md](docs/integrations.md) for setup and routing details.

## Install

### Guided Install

```bash
curl -fsSL https://spotlight.buriedsignals.com/install-spotlight.sh | bash
```

One static, reviewable script ([`install-spotlight.sh`](install-spotlight.sh))
for every install. It opens a local configurator page in the browser — served
from `127.0.0.1` by `install/setup_server.py`. Runtime, API keys, optional
integrations, and install/vault paths (with a native folder picker) are all
collected there; keys are verified live with each provider and written to local
files with owner-only permissions. **API keys never transit Buried Signals
infrastructure and never exist in any downloadable artifact.** The hosted pages
are static and contain no forms; the configurator accepts POSTs only on the
loopback interface, guarded by a per-run token.

Prefer a file? The hosted setup page offers `spotlight-install.zip`, whose
key-free bootstrap fetches and runs the same canonical script.

The installer:

- clones the Spotlight source,
- installs required command-line dependencies,
- writes local environment/config files,
- creates the active case workspace and knowledge-vault scaffold,
- registers the vault for local search,
- installs `spotlight`, `spotlight doctor`, and `spotlight update`,
- runs preflight and opens a personalized getting-started guide.

### Local Install

Clone the repo and run the same installer from the working tree:

```sh
git clone https://github.com/buriedsignals/spotlight.git
bash spotlight/install-spotlight.sh
```

### Headless / CI Install

`--headless` skips the configurator and reads pre-exported environment
variables. Load keys from a `0600` env file — never inline `export KEY=...`
commands, which would land the keys in shell history:

```sh
set -a; . keys.env; set +a   # keys.env is chmod 600
curl -fsSL https://spotlight.buriedsignals.com/install-spotlight.sh | bash -s -- --headless
```

## Runtimes

Two shapes, one source of truth (the skills + `agents/*.md` role files):

- **Frontier runtimes** (Claude Code, Codex CLI, Gemini CLI): the runtime *is* the
  harness — it loads the skills natively, gates are chat turns, nothing else installs.
- **Non-frontier** (local GGUFs and API providers like Fireworks/OpenRouter): the
  repo's **Flue-on-Pi harness** (`harness/flue/`) provides orchestration — native
  investigator/fact-checker subagents, durable resumable sessions, RLM distillation,
  and conversation compaction. The installer deploys the *same* harness the evals
  exercise.

Per-runtime wiring lives in [docs/runtimes.md](docs/runtimes.md). The
machine-readable contract lives in [AGENTS.md](AGENTS.md).

## Local Models

Local model selection is an implementation detail, not the product. Spotlight
can use cloud, ZDR, or local inference depending on the runtime and newsroom
policy. On the local tier the day-to-day interface is one command per
investigation turn — `spotlight <case-id> "<message>"` (re-run with the same id
to answer each gate; `spotlight-local --stop` stops the model servers) — and
switching model tiers (12b/26b/31b) is an `.env` edit
(`SPOTLIGHT_GGUF_PATH` + `SPOTLIGHT_MODEL_TIER`), not a reinstall. Details and
fit checks: [docs/runtimes.md](docs/runtimes.md).

## Documentation

| Doc | For |
|---|---|
| [docs/README.md](docs/README.md) | Operator manual entry point. |
| [docs/investigating.md](docs/investigating.md) | Pipeline phases, gates, cycles, readiness, and stall protocol. |
| [docs/fact-checking.md](docs/fact-checking.md) | Independent verification, SIFT, verdict taxonomy, and evidence trails. |
| [docs/epistemic-grounding.md](docs/epistemic-grounding.md) | Claim-to-evidence grounding and confidence caps. |
| [docs/monitoring.md](docs/monitoring.md) | Monitoring lifecycle across Mycroft, Scoutpost, and runtime fallbacks. |
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
- **Scoutpost** is hosted monitoring: scouts, information units, alerts, and
  durable follow-up on leads.

## Attribution

- Content Access and Social Media Intelligence skills adapt work from
  [jamditis/claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism)
  by Joe Amditis (Center for Cooperative Media). Web Archiving, originally adapted
  from the same collection, is now a full rewrite whose methodology is inspired by
  OpenSanctions evidence-preservation practice.
- Follow the Money synthesizes public investigative-finance methodology from
  Jim Shultz, GIJN, EBU, and related training material.
- Investigate includes methodology influenced by Bellingcat training material.

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
| **Knowledge vault** | [Obsidian](https://obsidian.md/) (the vault app) · [QMD](https://www.npmjs.com/package/@tobilu/qmd) (tobilu — local vault search) |
| **Reports & site** | [Mermaid](https://github.com/mermaid-js/mermaid) (MIT — report diagrams) · [Three.js](https://github.com/mrdoob/three.js) (mrdoob, MIT — the landing scene) |
| **Methodology** | [Joe Amditis](https://github.com/jamditis/claude-skills-journalism) (MIT) · [SIFT — Mike Caulfield](https://hapgood.us/2019/06/19/sift-the-four-moves/) · [Bellingcat](https://www.bellingcat.com/) · [GIJN](https://gijn.org/) (Patrucic & Cosic, CC BY-ND) · Jim Shultz — Follow the Money · [Derek Bowler · EBU](https://www.ebu.ch/) |

> Built something here we should credit, or want a listing changed or removed?
> Open an issue or PR — we'll fix it fast.

## License

See upstream plugin licenses. Spotlight additions by Buried Signals.
