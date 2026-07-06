# Integrations — Agent Runtimes

Spotlight's agnostic contract is `AGENTS.md` + `skills/*/SKILL.md`. Any agent runtime that can (a) read those files, (b) dispatch the 13 verbs to native tools, and (c) spawn sub-agents can run Spotlight.

This doc is the per-runtime wiring guide. Each section covers: how the runtime loads skills, how verbs map, how sub-agents work, and how sensitive mode is enforced.

---

## The verb contract (shared across all runtimes)

```
fetch, search, read-file, write-file, edit-file, list-files, grep-files,
execute-shell, spawn-agent, wait-agent, invoke-skill, query-vault, vault-write
```

Universal backings (never change):

| Verb | Concrete tool |
|---|---|
| `fetch`, `search` | `firecrawl` CLI (`firecrawl scrape`, `firecrawl search`) |
| `query-vault` | `BUN_INSTALL="" qmd query <vault> "<query>"` |
| `vault-write` | `obsidian` CLI (Obsidian app must be running) |
| `execute-shell` | native shell subprocess |
| `read-file`, `write-file`, `edit-file` | filesystem (runtime-native) |
| `list-files`, `grep-files` | glob + ripgrep (runtime-native) |

Runtime-specific backings (vary):

| Verb | Varies by runtime |
|---|---|
| `spawn-agent`, `wait-agent` | pi extension / Hermes `delegate_task` / Goose recipe / tmux subprocess / SDK call |
| `invoke-skill` | pi's native skill loader / Hermes SKILL.md injection / Goose recipe prepend / raw prompt concat |

---

## opencode

**What it is:** Terminal-first AI coding agent by Anomaly Innovations (https://opencode.ai). MIT license. Native `AGENTS.md`, `SKILL.md`, sub-agents, MCP, and a built-in `llama.cpp` provider that talks directly to a local llama-server. **The recommended local Spotlight runtime.**

### Install

```bash
brew install opencode                   # CLI (recommended)
brew install --cask opencode-desktop    # Optional GUI app
# or, no Homebrew:
curl -fsSL https://opencode.ai/install | bash
```

### Loading this repo

opencode discovers `SKILL.md` files **recursively** under each of these roots (the binary globs `skills/**/SKILL.md`; verified on opencode 1.17.5):

- Project: `.opencode/skills/`, `.claude/skills/`, `.agents/skills/`
- Global: `~/.config/opencode/skills/`, `~/.claude/skills/`, `~/.agents/skills/`

The **leaf** directory holding `SKILL.md` must equal the `name:` field in the frontmatter (validated); intermediate directories are ignored. So a per-product namespace subdir works: `~/.config/opencode/skills/spotlight/<skill>/SKILL.md` is discovered exactly like a top-level `<skill>/SKILL.md`.

Install Spotlight globally (namespaced under `spotlight/`, matching the engine's `<root>/<product>/<skill>` shape):

```bash
mkdir -p ~/.config/opencode/skills/spotlight
for skill_dir in /path/to/spotlight/skills/*/; do
  name=$(basename "$skill_dir")
  ln -sfn "$skill_dir" "$HOME/.config/opencode/skills/spotlight/$name"
done
```

Creates symlinks for all skills, including `spotlight`, `ingest`, `monitoring`, `acquisition-graduation`, `web-archiving`, `content-access`, `epistemic-grounding`, `shell-safety`, `osint`, `investigate`, `follow-the-money`, `social-media-intelligence`, `integrations`, and `review`. Live links — `git pull` in the spotlight repo updates everything.

`AGENTS.md` is loaded as Rules (https://opencode.ai/docs/rules/), walked up from cwd to the git worktree. Drop a project `AGENTS.md` in your investigations directory and opencode picks it up automatically.

### Local llama.cpp provider config

Merge into `~/.config/opencode/opencode.json` (preserves any other providers you have):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "llama.cpp": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "llama-server (local)",
      "options": { "baseURL": "http://127.0.0.1:8080/v1" },
      "models": {
        "qwen27": {
          "name": "Qwen3.6-27B Uncensored (local llama.cpp, Q4_K_P)",
          "limit": { "context": 262144, "output": 16384 },
          "cost": { "input": 0, "output": 0 }
        }
      }
    }
  }
}
```

Start with: `opencode --model llama.cpp/qwen27` (or use the installer-generated launcher script).

### Local Ollama provider config

Ollama's OpenAI-compatible endpoint expects chat requests at `/v1/chat/completions`. Configure opencode with the OpenAI-compatible provider and keep `baseURL` at the `/v1` root:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local OpenAI-compatible)",
      "options": { "baseURL": "http://127.0.0.1:11434/v1" },
      "models": {
        "spotlight-gemma4-q4": {
          "name": "gemma-4-26B-A4B-it-GGUF (local Ollama)"
        }
      }
    }
  }
}
```

Do not use `ollama-ai-provider-v2` here. In opencode it calls `/v1/chat`, which Ollama answers with 404. Do not set `baseURL` to `/v1/chat/completions` either; the provider appends the chat completion route itself.

### Verb bindings

opencode ships native `bash`, `read`, `write`, `edit`, `grep`, `glob`, `multi-edit` — covers 8 of the 13 verbs directly. The remaining five shell out:

| Verb | Concrete tool |
|---|---|
| `fetch`, `search` | `firecrawl` CLI via `bash` |
| `query-vault` | `BUN_INSTALL="" qmd query` via `bash` |
| `vault-write` | `obsidian` CLI via `bash` |
| `invoke-skill` | opencode's native `skill` tool — agents see available skills and load them on demand |

### Sub-agents

**Native** (https://opencode.ai/docs/agents/) — Spotlight's `investigator` and `fact-checker` map directly to opencode agent files (markdown manifests with frontmatter). Each agent gets its own context, prompt, and optionally its own model.

### Sensitive mode

Enforce at the agent definition: strip `firecrawl` (and any external-fetch shell) from the agent's `allowed-tools` frontmatter. Same pattern as the Claude Code marketplace plugin.

---

## pi

**What it is:** Minimal TypeScript coding harness by Mario Zechner (https://pi.dev). MIT license. Spotlight setup installs the reviewed `@earendil-works/pi-coding-agent@0.79.6` pin when Pi is selected. Natively supports `AGENTS.md` + `skills/*/SKILL.md`.

**Status:** Local alternative. opencode (above) remains the recommended local Spotlight runtime because pi lacks native sub-agents (so investigator + fact-checker run single-context, weakening the verification independence guarantee). Spotlight's installer (`install-spotlight.sh`, with the runtime picked in the local configurator) offers pi as an explicit second choice — picked it when you want a leaner install and accept the sub-agent trade-off.

### Loading this repo

```bash
mkdir -p ~/.pi/agent/skills
ln -sfn /path/to/spotlight/skills ~/.pi/agent/skills/spotlight
pi
```

pi recursively walks `~/.pi/agent/skills/` (user) and `<cwd>/.pi/skills/` (project) at startup, picking up every `SKILL.md` it finds — verified in `pi-coding-agent/dist/core/skills.js:347-348`. Skill names come from each frontmatter, so the symlink above loads all Spotlight sub-skills by name.

`AGENTS.md` is layered into pi's system prompt from `~/.pi/agent/`, parent directories, and the current directory (per [pi.dev docs](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent)).

### Verb bindings

pi ships native `Read`, `Write`, `Edit`, `Grep`, `Glob`, `Bash` equivalents. The 13-verb contract maps directly — skills reference verbs by name and pi's model uses its native tools to execute (e.g. `execute-shell("firecrawl scrape <url>")` becomes a `Bash`-equivalent call).

### Sub-agents

**pi does not ship built-in sub-agents.** Workarounds: a `pi-subagent` extension if one exists, tmux-spawn a second pi process via RPC mode, or SDK-mode wrapper. For Spotlight's investigator/fact-checker pattern this is awkward — that's why opencode is the recommended local runtime.

### Local llama-server provider via pi

Use the `pi-llama-cpp` extension — a community package by `gsanhueza` (MIT, v0.4.0 as of May 2026), listed on Pi's official package registry at https://pi.dev/packages/pi-llama-cpp:

```bash
pi install npm:pi-llama-cpp
```

The extension auto-detects models on the running llama-server (`/models` endpoint), supports load/unload/switch via `/models` slash command, and resolves the endpoint URL from any of:

1. `.pi/llama-server.json` in the project root — `{"url":"http://127.0.0.1:8080"}`
2. `LLAMA_SERVER_URL` env var
3. `~/.pi/agent/settings.json` — `{"llamaServerUrl":"http://127.0.0.1:8080"}`
4. Default `http://127.0.0.1:8080`

Spotlight's installer writes option 3 automatically when you pick Pi as the agent harness — pointing at port 8080 for `llamacpp` or 11434 for Ollama. Pi's authentication for the local server (if any) lives in `~/.pi/agent/auth.json` under provider id `llama-server` (displayed as "Llama.cpp" in the Pi UI).

Status indicators on the `/models` browser: 🟢 loaded · 🟡 loading · 🔴 failed · 🔵 sleeping · ⚪ unloaded. The sleeping state requires `llama-server --sleep-idle-seconds <n>` on the server side.

opencode's native `llama.cpp` provider does the same model-browsing job via one JSON block in `opencode.json` — no extension. Either path connects an agent to the same OpenAI-compatible endpoint; pick the agent based on whether you need native sub-agents.

**Current Spotlight operator model**: `unsloth/gemma-4-26B-A4B-it-GGUF` on Hugging Face (base Gemma 4 26B A4B — we evaluated a journalism fine-tune but the base outperformed it on tool-use + document OCR). Multimodal (text + vision) VLM MoE — 26B total / 4B active. Native vision for scanned court documents, satellite imagery, and screenshots. Recommended quants:
- `gemma-4-26B-A4B-it-UD-Q6_K_XL.gguf` (~22 GB) + `mmproj-BF16.gguf` (~1.2 GB) — 48GB+ Macs
- `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf` (~18 GB, imatrix-calibrated by Unsloth) + `mmproj-BF16.gguf` — 24GB+ Macs

Serve via llama-server:
```bash
llama-server -m gemma-4-26B-A4B-it-UD-Q6_K_XL.gguf --mmproj mmproj-BF16.gguf \
  --port 8081 --ctx-size 16384 --n-gpu-layers 999
```

### Sensitive mode

Set `sensitive: true` in `AGENTS.md` frontmatter (or pass as env `SPOTLIGHT_SENSITIVE=true`). The orchestrator instructs pi to strip `fetch`/`search` from each agent's `allowed_verbs`. Implementation paths:

- Write a pi extension that intercepts tool calls and blocks `Bash(firecrawl …)` when the sensitive flag is on
- Or rely on the orchestrator's skill instructions to refuse calls in sensitive mode (less defense-in-depth but no code required)

---

## Hermes

**What it is:** Production ambient agent on the Mac Mini, loaded via `~/.hermes/config.yaml`. Already in use for the Mycroft workflow. See `~/.hermes/config.yaml` for the live config.

### Loading this repo

Add to `skills.external_dirs` in `~/.hermes/config.yaml`:

```yaml
skills:
  external_dirs:
    - /path/to/spotlight/skills
    # existing kit dirs follow
    - ~/buried_signals/kit/mycroft
    - ~/buried_signals/kit/shared
```

Restart Hermes:

```bash
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
```

All Spotlight skills (spotlight, ingest, monitoring, acquisition-graduation, web-archiving, content-access, epistemic-grounding, shell-safety, osint, investigate, follow-the-money, social-media-intelligence, integrations, review) become available by `invoke-skill` name.

### Verb bindings

| Verb | Hermes tool |
|---|---|
| `fetch`, `search` | shell call to `firecrawl` CLI |
| `read-file`, `write-file`, `edit-file` | Hermes filesystem tools |
| `execute-shell` | Hermes terminal |
| `query-vault` | `BUN_INSTALL="" qmd query` via terminal |
| `vault-write` | `obsidian` CLI via terminal |
| `spawn-agent` | `delegate_task()` with the agent prompt + iteration_limit |
| `wait-agent` | `delegate_task` is synchronous by default; handle = task id |
| `invoke-skill` | Hermes reads the SKILL.md file and injects into the active prompt |

### Sub-agents via delegate_task

The orchestrator calls `delegate_task` with a goal string composed from:

- `agents/investigator.md` (or `fact-checker.md`) prompt
- Mode flag (PLANNING / EXECUTION)
- Project context (VAULT_PATH, PROJECT, CYCLE)

Hermes' `delegation` block in config.yaml sets per-delegation model and iteration limit. The agent manifest in this repo declares `iteration_limit: 80` (investigator) and `50` (fact-checker) — map these to Hermes' `max_iterations`.

### Sensitive mode

Hermes has a `local-gemma` skill at `~/buried_signals/kit/mycroft/local-gemma/SKILL.md` that routes sensitive tasks to the llama-server on `127.0.0.1:8081` (fine-tuned Gemma 4 E4B journalist model). When Spotlight is invoked with `sensitive: true`:

1. The orchestrator sets the per-delegation model to `local-gemma` for all agent spawns
2. Hermes routes `fetch`/`search` verbs to a no-op or error — the agent works from local `{CASE_DIR}/research/`
3. The orchestrator marks findings as "sensitive-mode constrained" at Gate 1

---

## Goose (extension pack)

**What it is:** Block/Square's CLI agent (https://block.github.io/goose/). Ships as a brew/installer package; config at `~/.config/goose/config.yaml`. Extensions add capabilities.

**This repo is packaged as a Goose extension.** Consumers install once; all skills become available.

### Extension manifest

At the repo root (or a distribution artifact), provide a Goose extension descriptor:

```yaml
# extension.yaml (Goose extension format)
name: spotlight
version: "1.0"
description: "OSINT investigation system — verified findings, fact-checking, vault ingestion"
type: agent-pack
entry:
  agents_md: AGENTS.md
  skills_dir: skills/
  agent_prompts_dir: agents/
  schemas_dir: schemas/
requires:
  cli_tools:
    - firecrawl   # reviewed setup pin: firecrawl-cli@1.3.1
    - obsidian    # Obsidian app (optional, for vault-write)
    - qmd         # reviewed setup pin: @tobilu/qmd@2.5.3
  env_vars:
    required: [FIRECRAWL_API_KEY]
    optional: [OSINT_NAV_API_KEY, CORE_API_KEY]
recipes:
  - id: spotlight-investigate
    description: "Start a new OSINT investigation"
    entry_skill: spotlight
  - id: spotlight-ingest
    description: "Archive completed findings to a vault"
    entry_skill: ingest
```

*(Goose's extension format is evolving; verify the exact YAML shape against the current Goose docs before publishing. The fields above are the semantic contract — adjust key names to match Goose's live schema.)*

### Installing

Once published to a Goose extension registry (or a git URL):

```bash
goose extensions install spotlight
```

This should wire:

- `AGENTS.md` as the project-context file Goose loads at session start
- All skills under `skills/` discoverable via Goose's skill-search
- Agent prompts in `agents/` loadable as recipe variants
- Schemas validated automatically against case file writes

### Verb bindings

| Verb | Goose equivalent |
|---|---|
| `fetch`, `search` | Goose tool call to `firecrawl` (either as MCP server or raw subprocess) |
| `read-file`, `write-file`, `edit-file` | Goose filesystem tools |
| `execute-shell` | Goose developer-mode shell or restricted subprocess |
| `spawn-agent` | Goose recipe invocation — spawn a new session with the agent prompt |
| `wait-agent` | Goose sessions are synchronous; wait for completion |
| `invoke-skill` | Goose loads SKILL.md into the system prompt |

### Sub-agents via recipes

Each `agents/*.md` becomes a Goose recipe. The orchestrator skill (`spotlight/SKILL.md`) invokes recipes for investigator PLANNING, investigator EXECUTION, fact-checker pass. Recipe parameters: PROJECT, VAULT_PATH, CYCLE, INTEGRATIONS.

### Sensitive mode

Goose supports per-session provider routing. When `sensitive: true`:

- Orchestrator invokes recipes with a local provider binding (OpenAI-compatible endpoint to llama-server on 127.0.0.1:8081 or equivalent)
- `fetch`/`search` tool permissions are revoked at session start via Goose's tool allowlist
- Evidence must come from `{CASE_DIR}/research/` — agent cannot reach the network

---

## Codex CLI

**What it is:** OpenAI's CLI agent (`@openai/codex`, reviewed setup pin `0.138.0`). Reads `AGENTS.md` natively at session start (same convention as pi). Auth via ChatGPT Plus/Pro/Team, Codex free-tier login, or an OpenAI API key. A quick-start adapter bundle lives in `adapters/codex/`.

### Installing

```bash
npm install -g @openai/codex@0.138.0
codex login   # OAuth via ChatGPT OR set OPENAI_API_KEY
```

Point Codex at the repo root as its working directory. `AGENTS.md` is loaded automatically.

### Verb bindings

| Verb | Codex tool |
|---|---|
| `read-file`, `list-files`, `grep-files` | native file tools (no config needed) |
| `write-file`, `edit-file` | native edit tools — require `--sandbox workspace-write` or higher |
| `execute-shell` | `bash -lc` tool — require `--sandbox workspace-write` or higher |
| `fetch`, `search` | `execute-shell` wrapping the `firecrawl` CLI |
| `query-vault` | `execute-shell` wrapping `BUN_INSTALL="" qmd query` |
| `vault-write` | `execute-shell` wrapping the `obsidian` CLI |
| `invoke-skill` | natively loads `skills/{skill}/SKILL.md` when referenced |
| `spawn-agent`, `wait-agent` | `execute-shell` spawning a second `codex exec` subprocess — see below |

### Sandbox mode

On the host, keep Codex's default read-only sandbox and widen with `-s workspace-write` only when writes are needed. Spotlight no longer ships a Docker setup path; package-supply-chain control is handled by reviewed dependency pins in `VALIDATED_DEPENDENCIES.md`.

Local RLM/Ollama calls are the exception to ordinary read-only file work:
`integrations/rlm/run_rlm.py` must reach `http://127.0.0.1:11434`. If Codex's
sandbox blocks localhost, approve host localhost access or run that RLM call
outside the sandbox before concluding `gemma4:e4b` is unavailable.

### Sub-agents — `codex exec` subprocess pattern

Codex 0.122 has no first-class multi-agent primitive. Spotlight relies on isolation between `investigator` and `fact-checker` for the verification guarantee, so we run the sub-agent as a **separate `codex exec` subprocess** — each call is a fresh conversation with its own context window. The orchestrator:

1. Reads the target agent prompt (e.g. `agents/fact-checker.md`) + the skill instructions
2. Shells out via `execute-shell`:

```bash
codex exec \
  --ephemeral \
  --skip-git-repo-check \
  --profile fact-checker \
  --output-last-message /tmp/fact-checker.out \
  "MODE: VERIFY
PROJECT: {project}
VAULT_PATH: {vault}
CYCLE: {cycle}

<contents of agents/fact-checker.md>"
```

3. Reads the sub-agent's side-effects from the filesystem (`{CASE_DIR}/data/fact-check.json`) — the contract is file-based, not stdout.

`--ephemeral` keeps the sub-agent session off disk; `--profile fact-checker` loads the per-agent model + iteration budget from `~/.codex/config.toml` (see `adapters/codex/config.toml.example`). Iteration limits from the agent manifest (`iteration_limit: 80` investigator, `50` fact-checker) map to Codex's `max_output_tokens` + turn budget in the profile.

### Sensitive mode and local inference

Codex 0.122 ships a native `--oss` flag that detects Ollama on `127.0.0.1:11434`. Use it instead of a custom `[model_providers.*]` entry — that route is broken in 0.122 because Codex now requires `wire_api = "responses"` (see [codex#7782](https://github.com/openai/codex/discussions/7782)) which Ollama and llama-server do not speak.

```bash
SPOTLIGHT_SENSITIVE=true codex exec \
  --oss \
  --local-provider ollama \
  --model gemma-4-26B-A4B-it \
  --skip-git-repo-check \
  "<prompt>"
```

For defence-in-depth, wrap `firecrawl` in a shell alias that refuses to run when `SPOTLIGHT_SENSITIVE=true` — otherwise the orchestrator can still fetch external resources via `execute-shell`.

### Known limitations (v0.122)

- Rate-limited ChatGPT free tier will **not** complete a full investigation (expect ~10-20 turns before the daily cap). Use Plus/Pro or API for production.
- Model default is `gpt-5.4` under ChatGPT login; override per profile.
- `spawn-agent` via subprocess shares the OAuth token with the parent — no per-agent auth isolation. Rate limits apply to the sum of orchestrator + sub-agents.
- Tool-use falls apart on small models (< ~14B). `llama3.2:3b` technically advertises tools but will not call them — it answers "I don't see the file" instead of invoking `read_file`. Always target Gemma 4 26B A4B class or better for real runs.
- `[model_providers.*]` with `wire_api = "chat"` is rejected — use `--oss` for all local inference.

---

## Gemini CLI

**What it is:** Google's CLI agent with `activate_skill` tool. Reads `GEMINI.md` (symlink `GEMINI.md → AGENTS.md` if you want Gemini to see the same contract). Currently not installed on this machine.

### Loading

Point Gemini at the repo root. Create `GEMINI.md` as a symlink to `AGENTS.md` so Gemini's startup loader sees the contract.

### Verb bindings

Gemini's `activate_skill` tool maps to `invoke-skill`. Other verbs map to Gemini's native tools (file I/O, shell, web fetch).

### Sub-agents

Gemini's sub-agent support is evolving. Until native primitives stabilize, use the same tmux / SDK approach as pi.

---

## Local OpenAI-compatible endpoints

Any OpenAI-compatible `/v1/chat/completions` endpoint can drive Spotlight as long as the host harness (pi, Hermes, Goose, a thin SDK wrapper) supports the agent loop.

### Common endpoints

| Backing | URL | Use case |
|---|---|---|
| llama-server (llama.cpp) | `http://127.0.0.1:8080/v1` | Lean, Terminal-only — `brew install llama.cpp`. Default for the installer's local mode. |
| Ollama | `http://127.0.0.1:11434/v1` | CLI-first model manager — `brew install ollama`, `ollama pull <repo>`. |
| Exoscale Dedicated Inference | `https://exoscale-ci-…/v1` | Swiss-sovereign hosted inference |
| vLLM | `http://localhost:8000/v1` | High-throughput self-hosted |

### Wiring

The endpoint is configured at the harness layer (pi's `models.json`, Hermes' provider config, Goose's model settings). The skills in this repo are provider-agnostic — they assume the model can call the verb set; how inference is served is the harness's problem.

### Fine-tune compatibility

Spotlight agents use `preferred_model` in their manifest frontmatter. For a local fine-tune:

```yaml
preferred_model:
  claude: opus
  gemini: gemini-2.5-pro
  gpt: gpt-4o
  local: gemma-4-26B-A4B-it   # current ship — upstream base VLM with native vision
```

The adapter picks the `local` entry when the active provider is the local endpoint. If the fine-tune underperforms on methodology design (observed with sub-10B models per the sovereign-inference spec), the orchestrator warns the user and offers to route just the investigator PLANNING step to a stronger hosted model while keeping EXECUTION and fact-checking on the local fine-tune.

---

## Sensitive mode across runtimes

When `sensitive: true` is set in `AGENTS.md` (or via a runtime command), every adapter MUST strip `fetch` and `search` from each agent's `allowed_verbs`. The enforcement point varies:

| Runtime | Enforcement |
|---|---|
| pi | Extension intercepts tool calls + skill instruction refuses in-mode |
| Hermes | Tool allowlist + `local-gemma` skill routes to llama-server |
| Goose | Per-session tool allowlist revokes network tools |
| Codex | Native tool allowlist (per Codex config) |
| Gemini | Tool allowlist |
| Local-endpoint wrappers | Orchestrator refuses to call the verb backing; wrapper blocks the shell call |

A sensitive investigation cannot satisfy the "document trail" readiness criterion from external sources. The orchestrator marks the investigation as **sensitive-mode constrained** at Gate 1, and the Gate 1 summary notes which readiness criteria could not be evaluated live.

---

## Adding a new runtime

To add a runtime adapter doc:

1. Confirm the runtime can read `AGENTS.md` or equivalent project-context file
2. Map each of the 13 verbs to the runtime's native tools
3. Choose a sub-agent pattern (native, tmux, SDK wrapper)
4. Choose a sensitive-mode enforcement point
5. Write a new section here with the same structure as existing ones
6. If the runtime has a distribution format (Goose extension, npm package, Homebrew tap), add a manifest entry at the repo root

All runtimes share the same skill content. The adapter doc is 200–400 lines of mapping and setup — the skills themselves are never rewritten per runtime.
