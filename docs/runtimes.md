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

## pi

**What it is:** Minimal TypeScript coding harness by Mario Zechner (https://pi.dev). MIT license. `npm install -g @mariozechner/pi-coding-agent`. Natively supports `AGENTS.md` + `skills/*/SKILL.md`.

### Loading this repo

Drop the agnostic repo into pi's skill-search path, or symlink it:

```bash
ln -s /Users/you/buried_signals/spotlight ~/.pi/agent/spotlight
pi
```

pi auto-loads:

- `AGENTS.md` from `~/.pi/agent/`, parent directories, and the current directory (per [pi.dev docs](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent))
- `SKILL.md` files under the paths configured in pi's skill search

### Verb bindings

pi ships with most verbs built in: `Read`, `Write`, `Edit`, `Grep`, `Glob`, `Bash` equivalents. The 13-verb contract maps directly to pi's native toolset — no adapter code needed.

Skills in this repo reference verbs by name (e.g. `fetch(url, path)`). pi's model reads the skill and uses its native tools to execute. For example, a skill that says `execute-shell("firecrawl scrape <url>")` becomes a `Bash`-equivalent call in pi.

### Sub-agents

**pi does not ship built-in sub-agents.** Three options:

1. **Install a pi extension** that provides sub-agent spawning. Check [pi packages](https://pi.dev/packages) for `pi-subagent`, `pi-sdk-subagent`, or equivalent.
2. **tmux spawn** — spawn a second `pi` process in a tmux pane; pipe the prompt via RPC mode (`pi -p "<prompt>" --mode json`).
3. **SDK mode** — programmatically invoke pi as an SDK from a wrapper script that drives the orchestrator loop and calls pi for sub-agents.

For our fact-checker/investigator pattern, option 3 (SDK harness + sub-pi calls) is cleanest. Option 2 is simplest to prototype.

### Local fine-tune via pi

Add a custom provider in pi's `models.json` to route inference to your own OpenAI-compatible endpoint:

```json
{
  "providers": {
    "local-journalist": {
      "baseURL": "http://127.0.0.1:8081/v1",
      "apiKey": "unused",
      "models": ["gemma-4-26B-A4B-it"]
    }
  }
}
```

Then `/model local-journalist/gemma-4-26B-A4B-it` in pi, or bind it as default. This works for any local OpenAI-compatible server: llama-server (llama.cpp), Ollama (add `"baseURL": "http://127.0.0.1:11434/v1"`), vLLM, or a hosted Exoscale endpoint.

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

**What it is:** Production ambient agent on the Mac Mini, loaded via `~/.hermes/config.yaml`. Already in use for the Mycroft workflow. See `/Users/tomvaillant/buried_signals/mycroft/.hermes/config.yaml` for the live config.

### Loading this repo

Add to `skills.external_dirs` in `~/.hermes/config.yaml`:

```yaml
skills:
  external_dirs:
    - /Users/tomvaillant/buried_signals/spotlight/skills
    # existing kit dirs follow
    - ~/buried_signals/kit/mycroft
    - ~/buried_signals/kit/shared
```

Restart Hermes:

```bash
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
```

All 10 Spotlight skills (spotlight, investigator, fact-checker, ingest, monitoring, web-archiving, content-access, osint, investigate, follow-the-money, social-media-intelligence) become available by `invoke-skill` name.

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
2. Hermes routes `fetch`/`search` verbs to a no-op or error — the agent works from local `cases/{project}/research/`
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
    - firecrawl   # npm install -g firecrawl-cli
    - obsidian    # Obsidian app (optional, for vault-write)
    - qmd         # brew install qmd (optional, for query-vault)
  env_vars:
    required: [FIRECRAWL_API_KEY]
    optional: [OSINT_NAV_API_KEY, ACLED_API_KEY, ACLED_EMAIL, CORE_API_KEY]
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
- Evidence must come from `cases/{project}/research/` — agent cannot reach the network

---

## Codex CLI

**What it is:** OpenAI's CLI agent. Reads `AGENTS.md` natively at session start (same convention as pi). Currently not installed on this machine — this section is forward-looking.

### Loading

Point Codex at the repo root as the working directory. `AGENTS.md` is loaded automatically per Codex's convention.

### Verb bindings

Map the 13 verbs to Codex's native tool set (similar shape to pi). Document concrete mappings here once Codex is installed and its tool names confirmed.

### Sub-agents

Codex has first-class multi-agent primitives (per OpenAI's published specs). Use them to satisfy `spawn-agent` / `wait-agent`.

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
| llama-server (llama.cpp) | `http://127.0.0.1:8081/v1` | Local fine-tunes (Gemma 4 journalist, Qwen 3.6, etc.) |
| Ollama | `http://127.0.0.1:11434/v1` | Quick-switch between models, CLI-first |
| LM Studio | `http://127.0.0.1:1234/v1` | GUI-first model management — recommended for journalists not at home in Terminal |
| Exoscale Dedicated Inference | `https://exoscale-ci-…/v1` | Swiss-sovereign hosted inference |
| vLLM | `http://localhost:8000/v1` | High-throughput self-hosted |

**LM Studio wiring**: install via `brew install --cask lm-studio` (or download from [lmstudio.ai](https://lmstudio.ai)). On first launch the app installs the `lms` CLI at `~/.lmstudio/bin/lms`. Use the **Discover** tab to search and download GGUF models from Hugging Face (e.g. `unsloth/Qwen3.6-35B-A3B-GGUF`), then open the **Developer** tab and click **Start Server** — the OpenAI-compatible endpoint comes up on `127.0.0.1:1234`. Point pi's `models.json` (or Hermes / Goose provider config) at that URL with any non-empty `apiKey` string (LM Studio doesn't authenticate by default).

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
