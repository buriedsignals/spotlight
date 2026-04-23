# Spotlight — Documentation

Spotlight is a runtime-agnostic OSINT investigation system — 11 skills, 2 agents, 5 schemas, and investigation-scoped monitoring orchestration. Point your agent runtime (pi, Hermes, Goose, Codex, Gemini, or any OpenAI-compatible harness) at this repo and run full-fidelity investigations end-to-end.

This `docs/` directory is the operator manual. `AGENTS.md` at the repo root is the machine-readable contract your runtime loads at startup.

## Reading order

If you are new to Spotlight, read in this order:

1. **[structure.md](structure.md)** — how this repo is laid out; what each file is for; the 13-verb contract
2. **[runtimes.md](runtimes.md)** — how to wire Spotlight into pi, Hermes, Goose, Codex, Gemini, or a local OpenAI-compatible fine-tune
3. **[integrations.md](integrations.md)** — external OSINT tool integrations (browser-use, Junkipedia, OSINT Navigator), manifest contract, preflight
4. **[investigating.md](investigating.md)** — the investigation pipeline: brief, methodology, cycles, gates, readiness, stall protocol
5. **[fact-checking.md](fact-checking.md)** — the independent verification pass: SIFT, verdict taxonomy, evidence trails
6. **[monitoring.md](monitoring.md)** — monitoring orchestration across Mycroft, coJournalist, and runtime-native fallbacks
7. **[recovery.md](recovery.md)** — when things break: agent crashes, corrupted files, stale locks, API failures

## 60-second quick-start

### pi (MIT TypeScript harness — https://pi.dev)

pi natively reads `AGENTS.md` + `skills/*/SKILL.md`. Drop this repo at `~/.pi/agent/` (or symlink it), launch `pi`, and the skills load automatically.

```bash
ln -s /Users/you/buried_signals/spotlight ~/.pi/agent/spotlight
pi
# > Start a Spotlight investigation on {lead}.
```

Configure a local fine-tune provider in pi's `models.json` to route inference to your own OpenAI-compatible endpoint (llama-server, Ollama, Exoscale, vLLM) — see [runtimes.md](runtimes.md#pi).

### Hermes (Mac Mini ambient agent)

Edit `~/.hermes/config.yaml`:

```yaml
skills:
  external_dirs:
    - /Users/you/buried_signals/spotlight/skills
```

Restart Hermes. The orchestrator is invocable via `invoke-skill("spotlight")`. Sensitive work routes to `local-gemma` (llama-server on 127.0.0.1:8081).

### Goose (extension pack)

Package this repo as a Goose extension. See [runtimes.md](runtimes.md#goose) for the extension manifest and recipe entry point.

### Codex CLI / Gemini CLI

Both read `AGENTS.md` natively. Point them at the repo root as the project context. Verb bindings mirror pi's.

## Claude Code note

The existing `buriedsignals/spotlight@1.2.1` marketplace plugin at `~/buried_signals/tools/skills/spotlight/` is the Claude Code path. It is **not** served from this repo — the agnostic repo exists precisely so non-Claude runtimes have the same capability without a Claude plugin dependency.

## The invariants (what never changes across runtimes)

Every runtime must preserve these — they are the editorial contract:

- Pipeline: Preflight → Brief → Methodology → Execution → Gate 1 → Ingestion
- Cycle limit 5, stall protocol on N ≥ 5 without readiness
- Six readiness criteria (findings, independence, disputes, affected perspective, document trail, gap assessment)
- Investigator modes: PLANNING and EXECUTION
- Fact-checker: SIFT methodology, verdicts `verified | unverified | disputed | false`, independent spawn
- Evidence grounding: scrape-before-cite, `local_file` on every source, archive before cite
- `access_method` enum and its confidence caps
- Archive hierarchy: Wayback → Archive.today → local

These live in `AGENTS.md`, `skills/spotlight/SKILL.md`, and the agent prompts. See [investigating.md](investigating.md) for the details.
