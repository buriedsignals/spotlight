# Spotlight

Runtime-agnostic OSINT investigation system for journalists. Verified findings, independent fact-checking, knowledge vault ingestion — driven by any agent harness that can read `AGENTS.md` and dispatch 13 abstract verbs.

## Install (for journalists)

**Open [`setup.html`](setup.html) in any browser.** Pick your runtime, paste your Firecrawl and OSINT Navigator API keys, optionally select integrations (browser-use, Junkipedia, Unpaywall), and click Generate. Browser Harness is detected by preflight when its CLI is installed. You'll get two install options:

- **Copy into Terminal** (simplest) — click Copy, ⌘+Space → Terminal → ⌘+V → Return
- **Download installer** — `spotlight-setup.zip` → extract → double-click the `.command` file (macOS Gatekeeper first-time: right-click → Open → Confirm)

The generated installer handles everything: clones this repo, installs `firecrawl-cli` and QMD, installs your chosen runtime, sets up the local model (if Local selected), writes `.env` with your keys (chmod 600), creates the vault scaffold, registers the vault for local search, and runs preflight. Works on macOS and Linux; Windows requires WSL.

The separate **Download agent setup** ZIP contains the same local setup choices and API key values in a private manifest. It is meant for a local agent to perform the same installation without asking you to paste secrets into chat. Keep it private like the command installer.

The installer creates:

- `spotlight` — launch the selected runtime from the Spotlight repo
- `spotlight doctor` — verify install, env names, runtime, vault, QMD, and preflight
- `spotlight update` — fetch `origin main`, fast-forward only, then run doctor

See [docs/integrations.md](docs/integrations.md) for the full setup flow and what happens behind the scenes.

## Local models — what we ship, and why

When you pick **Local** mode in the setup form, you get a choice of two abliterated journalism models. The picker exposes only these two; everything else has been benched and dropped.

| Tier | Model | RAM | Notes |
|------|-------|-----|-------|
| **Default** | [`tomvaillant/qwen3.5-9b-abliterated-journalist-GGUF:Q4_K_M`](https://huggingface.co/tomvaillant/qwen3.5-9b-abliterated-journalist-GGUF) | 16 GB | 9B dense, ~6 GB on disk, ~8 GB at runtime |
| **Heavy** (fit-check gated) | [`tomvaillant/qwen3.6-27b-abliterated-journalist-GGUF:Q4_K_M`](https://huggingface.co/tomvaillant/qwen3.6-27b-abliterated-journalist-GGUF) | 32 GB minimum | 27B dense, ~15 GB on disk, ~22 GB at runtime, thinking mode |

### Why these two

Both are **abliterated** — the refusal-vector has been removed so the model answers OSINT-grade prompts (corporate beneficial-ownership chains, surveillance technique verification, metadata forensics, etc.) instead of hedging or refusing. A stock instruction-tuned model is unsuitable for Spotlight regardless of size; abliteration is the editorial requirement.

The 9B is Tom's own fine-tune on the [investigative-journalism-training corpus](https://huggingface.co/datasets/tomvaillant/investigative-journalism-training): SIFT methodology, primary-source preference, OPSEC awareness, OSINT tool recall. On a 30-prompt eval against the bundled `eval/prompts.jsonl` suite (tool recommendations, methodology, ethics-opsec, refusal probes), the 9B scored **83.5 / 100** composite — 100% refusal-resistance, 100% directness. It outscored Tom's own 8B Gemma 4 E4B fine-tune (82.3) primarily on hedging behavior, and was the strongest model in its RAM tier we could measure.

The 27B is Tom's journalist fine-tune on the same investigative-journalism corpus as the 9B, built on huihui-ai's abliterated Qwen 3.6 base (the team that pioneered the abliteration technique). Ships as a HuggingFace GGUF; Ollama pulls it via the `hf.co/` passthrough using the standard Q4_K_M quant.

Head-to-head on a 5-prompt subset of the same eval suite, **the raw Huihui 27B base scored 100.0 composite vs the 9B's 91.2.** Same 100% refusal-resistance, same 100% directness, but ~3× more tool URLs per response (mean 5.2 vs 1.8), zero hedge markers, slightly more concise (480 median words vs 752). The journalist fine-tune inherits that base behavior and adds the corpus-specific tool/methodology recall. The size cost is justified when the journalist has the hardware.

### 27B runs in thinking mode (was: /no_think; we reversed)

Earlier revisions of this installer wrote an Ollama Modelfile TEMPLATE override that injected `/no_think` into every user message, plus opencode's `limit.output: 16384` to cap reasoning budget. We removed the TEMPLATE override and kept only the output limit.

**The abliterated Qwen 3.6 family's `/no_think` codepath is damaged** — verified empirically on Huihui base + Tom's journalist fine-tune at Q4_K_M with Qwen-recommended sampling: output collapses into multilingual token soup and lock-loops within ~200 tokens. Probable cause: abliteration calibration covered only the thinking codepath, leaving the no-think branch with broken refusal-direction subtraction; quantization amplifies it.

We now keep the model's native chat template (thinking on) and rely on opencode's `limit.output: 16384` to give enough budget for Qwen's ~10k reasoning + ~5k content. Expect ~3-5× slower per-prompt vs the 9B. If reasoning blocks crowd out content, raise the output limit further.

### What got dropped, and why

- `unsloth/gemma-4-26B-A4B-it-GGUF:UD-Q4_K_M` — 17 GB MoE blob that OOMs on 16 GB Macs despite "active params" being 3.8B (all experts have to stay resident). The previous "Minimum Spotlight" label was misleading.
- `tomvaillant/gemma4-e4b-abliterated-journalist-GGUF` — superseded by the 9B; same RAM tier, marginally worse bench score, more verbose responses with one refusal hedge in 30 prompts.
- `HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive:IQ2_M` — failed to load via Ollama on our test machine. Likely IQ2_M+mmproj incompatibility rather than the model itself being broken (the variant has 341 likes and works via llama.cpp directly), but the install-form contract is "user pulls the recommended model and it just works."
- `huihui_ai/Qwen3.6-abliterated:27b` (raw Huihui base via Ollama-native tag) — superseded by Tom's journalist fine-tune on the same base. Same on-disk footprint, better bench scores on investigative-journalism prompts, identical thinking-mode behavior.

### Heavy-tier fit-check enforcement

The 27B needs 32 GB unified memory to run with headroom for Spotlight's orchestration pipeline (browser automation, scrape, vault writes, fact-check sub-agent). When the user picks the 27B card in `setup.html`, the form auto-triggers the `navigator.deviceMemory` + WebGPU probe; if RAM reports under 32 GB the card is flagged red with "switch to the 9B" copy. The user can still proceed, but is warned the install will OOM.

### Bench artefacts

The bench harness is at [`tools/fine-tuning/eval/`](https://github.com/buriedsignals/spotlight/tree/main/tools/fine-tuning/eval) — 30-prompt suite, per-model runs as JSONL, composite scoring via `scripts/spotlight_bench.py` (refusal-resistance × 0.45 + directness × 0.20 + concreteness × 0.20 + hedge penalty × 0.15). Re-run when you add or replace a model: `python3 scripts/eval.py --prompts eval/prompts.jsonl --output eval/runs/<model>.jsonl --endpoint http://127.0.0.1:11434/v1 --model <ollama-tag>`.

## What this is

An **agnostic port** of the `buriedsignals/spotlight@1.2.1` and `buriedsignals/osint@3.5.0` Claude Code plugins into a runtime-neutral form. The original plugins stay at `~/buried_signals/tools/skills/{spotlight,osint}/` as the canonical reference. This repo is the base that plugs into everything else.

## Supported runtimes

| Runtime | Status | How it loads |
|---|---|---|
| **opencode** (https://opencode.ai) | Primary local agent — native sub-agents | `brew install opencode` (CLI) or `brew install --cask opencode-desktop` (GUI). Symlink loop into `~/.config/opencode/skills/`. `AGENTS.md`, sub-agents, MCP all native. Pair with `llama.cpp` provider for fully-local Qwen via llama-server. |
| **pi** (https://pi.dev) | Alternative local agent — no sub-agents | `npm install -g @mariozechner/pi-coding-agent` + `pi install npm:pi-llama-cpp`. setup.html offers this as a second local agent. Investigator + fact-checker share one context — weaker independence than opencode. |
| **Claude Code** | Install package | `npm install -g @anthropic-ai/claude-code`; runs from repo dir |
| **Codex CLI** | Install package | `npm install -g @openai/codex`; reads `AGENTS.md` natively |
| **Gemini CLI** | Install package | `npm install -g @google/gemini-cli`; symlink `GEMINI.md → AGENTS.md` |
| **Hermes** (Mycroft / Mac Mini) | Production | `skills.external_dirs` in `~/.hermes/config.yaml` |
| **Goose** | Extension pack | `goose extensions install spotlight` |

Per-runtime wiring: **[docs/runtimes.md](docs/runtimes.md)**.

## What you get

- **Investigation pipeline**: Preflight → Brief → Methodology → 5 Execution cycles → Gate 1 → Ingestion
- **Independent fact-checking**: fact-checker spawned per cycle, SIFT methodology, 4-verdict taxonomy
- **6 readiness criteria**: enforced before Gate 1 — min findings, source independence, no unresolved disputes, affected perspective, document trail, gap assessment
- **Evidence grounding**: scrape-before-cite, every source has a `local_file`, archive hierarchy Wayback → Archive.today → local
- **15 skills**: orchestrator (spotlight), review (post-Gate-1 HTML feedback loop), integrations (routing), ingest, monitoring, provenance-signing, acquisition-graduation, web-archiving, content-access, epistemic-grounding, shell-safety, osint, investigate, follow-the-money, social-media-intelligence
- **7 external integrations shipped**: Browser Harness (browser acquisition fallback), browser-use (optional AI browser automation), Junkipedia (narrative tracking), Noosphere C2PA (optional case provenance signing), OSINT Navigator (tool discovery), Scoutpost (durable monitoring), Unpaywall (academic open access). Framework accepts more — see [docs/integrations.md](docs/integrations.md).
- **Monitoring orchestration**: passive signals from Mycroft plus durable monitors from Scoutpost or runtime-native routines
- **Knowledge vault ingestion**: Markdown vaults for Obsidian or Tolaria, with directory fallback; atomic registry updates; lock-file concurrency
- **Sensitive mode**: strips `fetch`/`search` from agents; investigation runs local-only
- **opencode-native + Hermes-native**: zero adapter code needed for these runtimes; markdown-only contract for others

## Dependencies

Required:
- **firecrawl** CLI — the universal backing for `fetch`/`search`. `npm install -g firecrawl-cli`; set `FIRECRAWL_API_KEY`. (Handled automatically by setup.html's generated installer.)

Also installed by setup:
- **qmd** — required for `query-vault` and vault memory. `BUN_INSTALL="" qmd query`.

Optional:
- **obsidian** CLI — for `vault-write` into an Obsidian vault.
- **Tolaria** — optional Markdown/YAML vault app; setup.html can download the latest macOS release when selected.
- **Python 3.11+** — for integrations preflight and optional local helper scripts.
- **Mycroft source-specific keys** — only if you also use Mycroft passive monitoring; for example `ACLED_API_KEY` + `ACLED_EMAIL` for ACLED in Mycroft.
- **OSINT_NAV_API_KEY** — for expanded OSINT tool discovery via OSINT Navigator.
- **JUNKIPEDIA_API_KEY** — for narrative / misinformation tracking (application-based at junkipedia.org).
- **CORE_API_KEY** — for academic paper access in `content-access` skill.
- **NOOSPHERE_C2PA_URL** — optional local or hosted Noosphere signer endpoint for C2PA provenance signing; no API key is required, but the signer must have its own signing credential configured.
- **Inference backend (for Local mode)** — `brew install llama.cpp` (lean, what setup.html defaults to) or `brew install ollama` (CLI-first model manager). Orthogonal to the agent choice: pick one of each. setup.html offers **opencode** (recommended, native sub-agents) or **Pi** (minimal, `pi install npm:pi-llama-cpp` extension) as the agent layer.

## Documentation

| Doc | For |
|---|---|
| **[docs/README.md](docs/README.md)** | Start here — entry point and quick-start per runtime |
| **[docs/structure.md](docs/structure.md)** | Repo layout, 13-verb registry, how to extend |
| **[docs/runtimes.md](docs/runtimes.md)** | Per-runtime wiring — pi, Hermes, Goose, Codex, Gemini, local OAI |
| **[docs/integrations.md](docs/integrations.md)** | External tool integrations (Browser Harness, browser-use, Junkipedia, Noosphere C2PA, OSINT Navigator, Unpaywall), setup flow, manifest contract |
| **[docs/investigating.md](docs/investigating.md)** | Pipeline phases, gates, cycles, readiness, stall protocol |
| **[docs/fact-checking.md](docs/fact-checking.md)** | Independence, SIFT, verdict taxonomy, evidence trails |
| **[docs/monitoring.md](docs/monitoring.md)** | Monitoring lifecycle across Mycroft, Scoutpost, and runtime-native fallbacks |
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
