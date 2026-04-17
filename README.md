# Spotlight

Runtime-agnostic OSINT investigation system for journalists. Verified findings, independent fact-checking, knowledge vault ingestion — driven by any agent harness that can read `AGENTS.md` and dispatch 13 abstract verbs.

## Install (for journalists)

**Open [`setup.html`](setup.html) in any browser.** Pick your runtime, paste your Firecrawl API key, optionally select integrations (browser-use, Junkipedia, OSINT Navigator), and click Generate. You'll get two install options:

- **Copy into Terminal** (simplest) — click Copy, ⌘+Space → Terminal → ⌘+V → Return
- **Download installer** — `spotlight-setup.zip` → extract → double-click the `.command` file (macOS Gatekeeper first-time: right-click → Open → Confirm)

The generated installer handles everything: clones this repo, installs `firecrawl-cli`, installs your chosen runtime, sets up the local model (if Local selected), writes `.env` with your keys (chmod 600), runs preflight. Works on macOS and Linux; Windows requires WSL.

See [docs/integrations.md](docs/integrations.md) for the full setup flow and what happens behind the scenes.

## What this is

An **agnostic port** of the `buriedsignals/spotlight@1.2.1` and `buriedsignals/osint@3.5.0` Claude Code plugins into a runtime-neutral form. The original plugins stay at `~/buried_signals/tools/skills/{spotlight,osint}/` as the canonical reference. This repo is the base that plugs into everything else.

## Supported runtimes

| Runtime | Status | How it loads |
|---|---|---|
| **pi** (https://pi.dev) | Primary — native support | Symlink repo into `~/.pi/agent/`; pi reads `AGENTS.md` + `skills/*/SKILL.md` natively |
| **Claude Code** | Install package | `npm install -g @anthropic-ai/claude-code`; runs from repo dir |
| **Codex CLI** | Install package | `npm install -g @openai/codex`; reads `AGENTS.md` natively |
| **Gemini CLI** | Install package | `npm install -g @google/gemini-cli`; symlink `GEMINI.md → AGENTS.md` |
| **Hermes** (Mycroft / Mac Mini) | Production | `skills.external_dirs` in `~/.hermes/config.yaml` |
| **Goose** | Extension pack | `goose extensions install spotlight` |
| **Local OpenAI-compatible** | Via harness | Ollama or llama-server + pi (or Hermes) — journalist-friendly setup via setup.html |

Per-runtime wiring: **[docs/runtimes.md](docs/runtimes.md)**.

## What you get

- **Investigation pipeline**: Preflight → Brief → Methodology → 5 Execution cycles → Gate 1 → Ingestion
- **Independent fact-checking**: fact-checker spawned per cycle, SIFT methodology, 4-verdict taxonomy
- **6 readiness criteria**: enforced before Gate 1 — min findings, source independence, no unresolved disputes, affected perspective, document trail, gap assessment
- **Evidence grounding**: scrape-before-cite, every source has a `local_file`, archive hierarchy Wayback → Archive.today → local
- **11 skills**: orchestrator (spotlight), review (post-Gate-1 HTML feedback loop), integrations (routing), ingest, monitoring, web-archiving, content-access, osint, investigate, follow-the-money, social-media-intelligence
- **3 external integrations shipped**: browser-use (AI browser automation), Junkipedia (narrative tracking), OSINT Navigator (tool discovery). Framework accepts more — see [docs/integrations.md](docs/integrations.md).
- **Feed monitoring**: pluggable sources (GDELT, RSS investigative, RSS regional, GDACS, ACLED) with preflight checks
- **Knowledge vault ingestion**: Obsidian-native (wikilinks) with directory fallback; atomic registry updates; lock-file concurrency
- **Sensitive mode**: strips `fetch`/`search` from agents; investigation runs local-only
- **Pi-native + Hermes-native**: zero adapter code needed for these runtimes; markdown-only contract for others

## Dependencies

Required:
- **firecrawl** CLI — the universal backing for `fetch`/`search`. `npm install -g firecrawl-cli`; set `FIRECRAWL_API_KEY`. (Handled automatically by setup.html's generated installer.)

Optional:
- **qmd** — for `query-vault`. `BUN_INSTALL="" qmd query`.
- **obsidian** CLI — for `vault-write` into an Obsidian vault.
- **Python 3.11+** — for the feed framework (`monitoring/feeds/`) and integrations preflight.
- **ACLED_API_KEY + ACLED_EMAIL** — for conflict event monitoring (free registration at https://developer.acleddata.com/).
- **OSINT_NAV_API_KEY** — for expanded OSINT tool discovery via OSINT Navigator.
- **JUNKIPEDIA_API_KEY** — for narrative / misinformation tracking (application-based at junkipedia.org).
- **CORE_API_KEY** — for academic paper access in `content-access` skill.
- **Ollama** (for Local runtime) — `brew install ollama`. Or llama.cpp (`brew install llama.cpp`) for power users.

## Documentation

| Doc | For |
|---|---|
| **[docs/README.md](docs/README.md)** | Start here — entry point and quick-start per runtime |
| **[docs/structure.md](docs/structure.md)** | Repo layout, 13-verb registry, how to extend |
| **[docs/runtimes.md](docs/runtimes.md)** | Per-runtime wiring — pi, Hermes, Goose, Codex, Gemini, local OAI |
| **[docs/integrations.md](docs/integrations.md)** | External tool integrations (browser-use, Junkipedia, OSINT Navigator), setup flow, manifest contract |
| **[docs/investigating.md](docs/investigating.md)** | Pipeline phases, gates, cycles, readiness, stall protocol |
| **[docs/fact-checking.md](docs/fact-checking.md)** | Independence, SIFT, verdict taxonomy, evidence trails |
| **[docs/monitoring.md](docs/monitoring.md)** | Feed framework, sources, preflight, scout lifecycle |
| **[AGENTS.md](AGENTS.md)** | Machine-readable runtime contract (verb registry, agent manifests, skill registry) |

## Source reference

Canonical source (read-only, never modified by this repo):

- `~/buried_signals/tools/skills/spotlight@1.2.1/` — original Spotlight Claude Code plugin
- `~/buried_signals/tools/skills/osint@3.5.0/` — original OSINT Claude Code plugin

Content in `skills/` is a verbatim port of these plugins with Claude-specific syntax (`Agent()`, `Skill()`, `WebFetch`, `Bash`, etc.) genericized to the 13 abstract verbs. Semantic invariants (readiness criteria, verdict taxonomy, SIFT, evidence grounding, gate sequencing) are preserved exactly.

## Attribution

- **Web Archiving** and **Content Access** skills adapted from [jamditis/claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism) by Jay Amditis (MIT License).
- **Social Media Intelligence** skill: same source.
- **Follow the Money** skill synthesizes methodology from Jim Shultz (Revenue Watch / Open Society Institute 2005), Jelter's "Follow the Money" presentation, Miranda Patrucic & Jelena Cosic (GIJN 2024, CC BY-ND 4.0), and Derek Bowler (EBU Eurovision News Spotlight 2025).
- **Investigate** skill includes methodology from Bellingcat training materials.

## License

See upstream plugin licenses. This repo's additions (verb mapping, docs, integrations framework, setup.html, feed preflight) are authored by Buried Signals — license TBD.
