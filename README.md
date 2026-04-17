# Spotlight

Runtime-agnostic OSINT investigation system for journalists. Verified findings, independent fact-checking, knowledge vault ingestion — driven by any agent harness that can read `AGENTS.md` and dispatch 13 abstract verbs.

## What this is

An **agnostic port** of the `buriedsignals/spotlight@1.2.1` and `buriedsignals/osint@3.5.0` Claude Code plugins into a runtime-neutral form. The original plugins stay at `~/buried_signals/tools/skills/{spotlight,osint}/` as the canonical reference. This repo is the base that plugs into everything else.

## Supported runtimes

| Runtime | Status | How it loads |
|---|---|---|
| **pi** (https://pi.dev) | Primary — native support | Symlink repo into `~/.pi/agent/`; pi reads `AGENTS.md` + `skills/*/SKILL.md` natively |
| **Hermes** (Mycroft / Mac Mini) | Production | `skills.external_dirs` in `~/.hermes/config.yaml` |
| **Goose** | Packaged as extension pack | `goose extensions install spotlight` |
| **Codex CLI** | Forward-looking | Reads `AGENTS.md` natively |
| **Gemini CLI** | Forward-looking | Symlink `GEMINI.md → AGENTS.md`; uses `activate_skill` |
| **Local OpenAI-compatible** | Via harness | llama-server, Ollama, Exoscale, vLLM — drive via pi, Hermes, or SDK |

Claude Code users stay on the marketplace plugin at `~/buried_signals/tools/skills/spotlight/` — this repo is for non-Claude runtimes.

Per-runtime wiring: **[docs/integrations.md](docs/integrations.md)**.

## 60-second quick-start

### pi

```bash
ln -s /Users/you/buried_signals/spotlight ~/.pi/agent/spotlight
pi
# > Start a Spotlight investigation on {your lead}.
```

### Hermes

Edit `~/.hermes/config.yaml`:

```yaml
skills:
  external_dirs:
    - /Users/you/buried_signals/spotlight/skills
```

Restart Hermes. Invoke with `invoke-skill("spotlight")`.

### Local fine-tune (any runtime)

Point the runtime at an OpenAI-compatible endpoint (llama-server on 127.0.0.1:8081, Ollama, Exoscale, etc.). Skills are provider-agnostic — how inference is served is the harness's problem. See `docs/integrations.md#local-openai-compatible-endpoints`.

## What you get

- **Investigation pipeline**: Preflight → Brief → Methodology → 5 Execution cycles → Gate 1 → Ingestion
- **Independent fact-checking**: fact-checker spawned per cycle, SIFT methodology, 4-verdict taxonomy
- **6 readiness criteria**: enforced before Gate 1 — min findings, source independence, no unresolved disputes, affected perspective, document trail, gap assessment
- **Evidence grounding**: scrape-before-cite, every source has a `local_file`, archive hierarchy Wayback → Archive.today → local
- **10 skills**: orchestrator, ingest, monitoring, web-archiving, content-access, osint, investigate, follow-the-money, social-media-intelligence
- **Feed monitoring**: pluggable sources (GDELT, RSS investigative, RSS regional, GDACS, ACLED) with preflight checks
- **Knowledge vault ingestion**: Obsidian-native (wikilinks) with directory fallback; atomic registry updates; lock-file concurrency
- **Sensitive mode**: strips `fetch`/`search` from agents; investigation runs local-only
- **Pi-native + Hermes-native**: zero adapter code needed for these runtimes; markdown-only contract for others

## Dependencies

Required:
- **firecrawl** CLI — the universal backing for `fetch`/`search`. `npm install -g @mendable/firecrawl-cli`; set `FIRECRAWL_API_KEY`.

Optional:
- **qmd** — for `query-vault`. `BUN_INSTALL="" qmd query`.
- **obsidian** CLI — for `vault-write` into an Obsidian vault.
- **Python 3.11+** — for the feed framework (`monitoring/feeds/`).
- **ACLED_API_KEY + ACLED_EMAIL** — for conflict event monitoring (free registration at https://developer.acleddata.com/).
- **OSINT_NAV_API_KEY** — for expanded OSINT tool discovery via OSINT Navigator.
- **CORE_API_KEY** — for academic paper access in `content-access` skill.

## Documentation

| Doc | For |
|---|---|
| **[docs/README.md](docs/README.md)** | Start here — entry point and quick-start per runtime |
| **[docs/structure.md](docs/structure.md)** | Repo layout, 13-verb registry, how to extend |
| **[docs/integrations.md](docs/integrations.md)** | Per-runtime wiring — pi, Hermes, Goose, Codex, Gemini, local OAI |
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

See upstream plugin licenses. This repo's additions (verb mapping, docs, adapter specs, feed preflight) are authored by Buried Signals — license TBD.
