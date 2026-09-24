# Integrations — Agent Runtimes

Spotlight's agnostic contract is `AGENTS.md` + `skills/*/SKILL.md`. A compatible local execution host must read those files, dispatch the 13 verbs to native tools, and isolate investigator/fact-checker sub-agents. Skill discovery alone does not establish that the full workflow succeeds.

This doc is the per-runtime wiring guide. Each section covers: how the runtime loads skills, how verbs map, how sub-agents work, and how sensitive mode is enforced.

## Project-local placement and execution host

Engine installs only the selected runtime's flat native leaves in the **actual
Spotlight checkout/runtime root**. Each leaf is `<skill-id>/SKILL.md`; there is
no extra product namespace and no home-global Spotlight fan-out.

| Selected local surface | Native root relative to checkout | Execution host |
|---|---|---|
| Pi / Flue | `.agents/skills/` | Checkout cwd; Flue explicitly selects `harness/flue/flue.config.ts` |
| Codex CLI | `.agents/skills/` | CLI launched in this checkout |
| ChatGPT Desktop | `.agents/skills/` | Codex → Local, this exact project |
| Claude Code / Claude Desktop | `.claude/skills/` | CLI checkout cwd / Code → Local, this exact project |
| Cursor | `.cursor/skills/` | This local project |
| Gemini CLI | `.gemini/skills/` | CLI checkout cwd |
| OpenCode | `.opencode/skills/` | CLI checkout cwd |
| Antigravity | `.agents/skills/` | This local project; copied leaves |
| Mycroft/Goose | `.agents/skills/` | Fresh Spotlight checkout session, not the Mycroft vault session |

Windows uses verified copies for every route; Antigravity uses verified copies
on other platforms too. Other routes use symlinks to verified private bundles.
The private store is `<Engine base>/skills/spotlight/bundles/`, with ledger
`<Engine base>/skills/spotlight/registry.json`. Mycroft remains in `~/.ok/skills`
with its shared ledger; OpenKnowledge remains the knowledge service/index,
not Spotlight skill storage.

The manifest seals `runtime` and `native_skill_ids`, including Navigator opt-out.
When enabled, Navigator comes from the catalog-pinned installed `navigator-cli`
distribution with verified version and skill bytes, not a checkout lookalike.
Launch requires active, committed ownership. Migration binds the plan hash and
unique apply commit identity to checked, retained recovery receipts. Unknown or
changed foreign objects are refused and retained. Recovery restores only proven
affected ownership and bytes, not a whole shared ledger. Inspect retained evidence
and use a reviewed Engine update; never hand-edit the ledger, construct receipts,
or blindly retry apply.

For unmanaged source development, create flat leaves only in the selected
project root using the [README setup](../README.md#install-from-source-agents).
This does not confer Engine ownership; use a separate checkout, never force
existing destinations, and do not expect automatic adoption.

All placement here requires the execution host to have local filesystem and
shell access to the same checkout, dependencies, and durable case storage.
Hosted inference through a local harness is compatible with that boundary.
External/cloud execution without those files is unsupported by this placement:
ordinary Desktop Chat/Cowork, remote modes, ChatGPT Cloud, and detached worktrees
are not interchangeable with the selected local project. Opening an app or
handing it a project is not evidence of runtime health.

### Acceptance boundary

Local proof covers Goose 1.50.0 discovering 25 intended skills in an isolated
checkout; Flue SDK discovery and actual sandbox cwd, dependency, and scoped-env
checks; mixed-ownership migration/update/uninstall preserving Mycroft and
synthetic Cases/Knowledge/graph bytes; and missing-browser repair followed by an
actual Crawl4AI raw-HTML task. It does **not** prove a complete model investigation,
native Windows, packaged Desktop, or the original timeout cause. The Windows VM
is deallocated and the failed extension identity is unknown. Full acceptance
remains blocked; the implementation is not a release or end-to-end health claim.

---

## The verb contract (shared across all runtimes)

```
fetch, search, read-file, write-file, edit-file, list-files, grep-files,
execute-shell, spawn-agent, wait-agent, invoke-skill, query-vault, vault-write
```

Universal backings (never change):

| Verb | Concrete tool |
|---|---|
| `fetch`, `search` | `fetch` → Crawl4AI via `integrations.scraping`; `search` → SearXNG via `integrations.search` (Firecrawl = optional fallback when `FIRECRAWL_API_KEY` is set) |
| `query-vault` | `scripts/query_vault.py`: exact workflows use local SQLite; broad discovery uses Open Knowledge directly with local receipt filtering |
| `vault-write` | Spotlight-local journaled projection writer (approved writes; no automatic fallback) |
| `execute-shell` | native shell subprocess |
| `read-file`, `write-file`, `edit-file` | filesystem (runtime-native) |
| `list-files`, `grep-files` | glob + ripgrep (runtime-native) |

`query-vault` is a same-user local adapter over Open Knowledge and Spotlight's
SQLite graph. It does not claim multi-user authorization isolation;
the adapter must surface that typed blocker rather than report live success.

Runtime-specific backings (vary):

| Verb | Varies by runtime |
|---|---|
| `spawn-agent`, `wait-agent` | pi extension / Hermes `delegate_task` / Goose recipe / tmux subprocess / SDK call |
| `invoke-skill` | pi's native skill loader / Hermes SKILL.md injection / Goose recipe prepend / raw prompt concat |

---

## Flue on Pi — the local & cloud non-frontier harness (CANONICAL, 2026-07-09)

**What it is:** the repo's `harness/flue/` — a [Flue](https://flueframework.com) app (TypeScript
harness framework built on Pi, by the Astro team) for the local and hosted-API
non-frontier routes. It uses the shared workflow skills and role files, native
investigator/fact-checker child sessions, checkout-discovered
`<cwd>/.agents/skills/` with bodies loaded on invoke, durable SQLite sessions,
and threshold conversation compaction. Current local compaction uses the
session model; the RLM sidecar serves source distillation.

### Managed launch

Use `bsig spotlight` (or Engine's installed `spotlight` launcher). Engine binds
the selected runtime and actual installed checkout, validates active ownership,
and supplies the configured case/model context and credentials at execution
time. It does not source a checkout `.env`.

For Flue, the process cwd and `SPOTLIGHT_CWD` are the checkout root, **not**
`harness/flue/`. Engine appends the explicit
`--config <checkout>/harness/flue/flue.config.ts` and refuses a caller-supplied
config override. Native discovery and the real host sandbox therefore use the
same root. `SPOTLIGHT_CASES_ROOT` and `SPOTLIGHT_ACTIVE_CASE` bind durable case
storage; the native state tools do not accept a model-selected sibling case.

Dependency and credential environments are scoped to the launched process.
Engine replaces inherited owned values, supplies `.spotlight/pydeps` through
`PYTHONPATH`, and adds the installed Navigator dependency/launcher paths when
available. Flue's local sandbox explicitly forwards the relevant dependency,
case, integration, and RLM environment; inheriting the outer process environment
alone is not sufficient. The role adapter resolves shared files from the
checkout and selects `SPOTLIGHT_PYTHON` (or the development `.venv` interpreter)
for Python seams. Do not replace missing dependencies with ad-hoc global paths.

### Unmanaged harness development

After explicitly provisioning dependencies, case storage, environment, and
project-local skills in a separate source checkout, launch from that root:

```bash
flue run spotlight --id my-case --config "$PWD/harness/flue/flue.config.ts"
```

This is a developer entry point, not an Engine installation or ownership repair.
Keep `SPOTLIGHT_CWD` equal to the actual checkout and supply the active-case
context required by the native tools. Resume with the same session id; human
gates still require explicit replies. No workflow-skill rewrite is required.

### Model tiers

Managed model changes use Engine's configuration/plan flow. For an unmanaged
development launch, configure its model server and harness environment explicitly:

| Var | Meaning |
|---|---|
| `SPOTLIGHT_FLUE_MODEL` | selected local or API-provider model |
| `SPOTLIGHT_MODEL_TIER` | `12b` \| `26b` \| `31b` — compaction profile (fold at ~16k/24.5k/28.5k, keep 4k/6k/8k recent) and raw-source affordance |
| `SPOTLIGHT_RLM_OPENAI_BASE_URL`, `SPOTLIGHT_RLM_OPENAI_MODEL` | optional source-distillation sidecar |
| `SPOTLIGHT_REASONING_BUDGET`, `SPOTLIGHT_COMPACT_AT`, `SPOTLIGHT_COMPACT_KEEP` | optional overrides of the tier defaults |

Changing environment values does not provision a model, dependencies, or an
Engine-owned installation.

### Cloud non-frontier (API providers) through the same harness

`harness/flue/src/app.ts` registers providers by env: **Fireworks** (`FIREWORKS_API_KEY`, GLM-5.2
ZDR) and **OpenRouter** (`OPENROUTER_API_KEY`). Select with
`SPOTLIGHT_FLUE_MODEL=fireworks/…` or `openrouter/…`; keep the same local checkout
cwd, explicit config, dependencies, and sandbox environment. Inference is remote,
but filesystem execution remains local. This does not support a remote harness
with no access to the installed project. Custom providers must declare
`contextWindow` and `maxTokens`.

### Verb bindings, sub-agents, sensitive mode

The `FLUE_VERB_ADAPTER` (`harness/flue/src/lib/roles.ts`) maps every contract verb to flue's
native tools and injects the absolute-path rules (PYTHONPATH + venv python for `integrations.*`,
absolute `CASE_DIR`). Sub-agents are **native** (`defineAgentProfile` + `task` delegation, own
child sessions). Role instructions come from the same runtime-agnostic `agents/<role>.md` files
the frontier path uses. Sensitive mode follows the skills (same as other runtimes).

---

## opencode

**Status: RETIRED as the local Spotlight runtime (2026-07-09)** — the Flue-on-Pi harness above
replaced it (native subagents + compaction + RLM in one app; the installer no longer writes an
opencode launcher for local mode). opencode remains usable as a generic runtime for the skills
(the wiring below is kept for reference) and as a cloud runtime choice.

**What it is:** Terminal-first AI coding agent by Anomaly Innovations (https://opencode.ai). MIT license. Native `AGENTS.md`, `SKILL.md`, sub-agents, MCP, and a built-in `llama.cpp` provider that talks directly to a local llama-server.

### Install

```bash
brew install opencode                   # CLI (recommended)
brew install --cask opencode-desktop    # Optional GUI app
# or, no Homebrew:
curl -fsSL https://opencode.ai/install | bash
```

### Loading this repo

Use flat leaves at `<checkout>/.opencode/skills/<id>/SKILL.md` and start OpenCode
from that exact checkout. Engine owns managed leaves; source developers use the
unmanaged project-local setup above. Do not publish a global
`spotlight/<id>` namespace or redirect OpenCode to Mycroft's shared store.

`AGENTS.md` supplies project rules from the checkout. Global provider settings
below configure inference, not Spotlight skill placement or ownership.

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

For an unmanaged local session, start `opencode --model llama.cpp/qwen27` from the checkout.

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
| `fetch`, `search` | `integrations.scraping` (Crawl4AI) / `integrations.search` (SearXNG) via `bash`; Firecrawl optional fallback |
| `query-vault` | Local graph for exact workflows; direct Open Knowledge discovery for broad queries |
| `vault-write` | Spotlight-local journaled projection writer |
| `invoke-skill` | opencode's native `skill` tool — agents see available skills and load them on demand |

### Sub-agents

**Native** (https://opencode.ai/docs/agents/) — Spotlight's `investigator` and `fact-checker` map directly to opencode agent files (markdown manifests with frontmatter). Each agent gets its own context, prompt, and optionally its own model.

### Sensitive mode

Enforce at the agent definition: strip `firecrawl` (and any external-fetch shell) from the agent's allowed tools. Native skill placement does not itself enforce network permissions.

---

## pi

**What it is:** Minimal TypeScript coding harness by Mario Zechner (https://pi.dev). MIT license. Natively supports `AGENTS.md` + `skills/*/SKILL.md`.

The canonical non-frontier harness is Flue on Pi, which supplies native
subagents, compaction, and durability. Direct Pi remains a selected local
skill-discovery route; its host must supply the required sub-agent isolation.

### Loading this repo

Use flat leaves at `<checkout>/.agents/skills/<id>/SKILL.md`, then launch Pi
from that checkout. Engine places the selected leaves; unmanaged source setup
uses the same local discovery shape without ownership receipts. Do not install
a product-root adapter under `~/.pi/agent/skills`.

`AGENTS.md` is layered into pi's system prompt from `~/.pi/agent/`, parent directories, and the current directory (per [pi.dev docs](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent)).

### Verb bindings

pi ships native `Read`, `Write`, `Edit`, `Grep`, `Glob`, `Bash` equivalents. The 13-verb contract maps directly — skills reference verbs by name and pi's model uses its native tools to execute (e.g. `execute-shell("firecrawl scrape <url>")` becomes a `Bash`-equivalent call).

### Sub-agents

Direct Pi requires a suitable extension or SDK wrapper for isolated workers. Flue supplies that layer for the canonical non-frontier route; discovery alone does not supply sub-agents.

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

For unmanaged direct-Pi development, configure the endpoint in the selected project or Pi's provider settings. This is inference configuration, not Engine skill ownership.

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

The checkout's current skill ids become available by `invoke-skill` name. This is an unmanaged Hermes integration, not an Engine-native placement route; its shell and workers must use the same local checkout.

### Verb bindings

| Verb | Hermes tool |
|---|---|
| `fetch`, `search` | shell call to `integrations.scraping` (Crawl4AI) / `integrations.search` (SearXNG); Firecrawl optional fallback |
| `read-file`, `write-file`, `edit-file` | Hermes filesystem tools |
| `execute-shell` | Hermes terminal |
| `query-vault` | Receipt-aware adapter; no runtime receives a raw OpenKnowledge MCP tool |
| `vault-write` | Knowledge Workspace Port with explicit approval and journal reconciliation |
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

## Goose

**What it is:** Block/Square's CLI agent (https://block.github.io/goose/), with
provider configuration at `~/.config/goose/config.yaml`. Goose remains a
supported historical Spotlight workflow route; the cutover changes placement,
not the investigation methodology or workflow skills.

### Loading this repo

Use flat leaves at `<checkout>/.agents/skills/<id>/SKILL.md` and start a fresh
Goose session in the actual Spotlight checkout. The selected Mycroft/Goose
Engine route reuses provider/key/local-server setup, but does not reuse the
Mycroft vault or welcome recipe as Spotlight's execution project. Desktop
handoff must likewise select the checkout for a fresh session.

No hypothetical extension descriptor or registry publication is required for
native skill discovery. Goose 1.50.0 discovered the 25 intended skills in the
isolated local checkout proof. That proves discovery, not a complete model
workflow, recipe execution, or packaged Desktop health. Existing Goose
workflow/recipe use does not require rewriting the shared skill content.

### Verb bindings

| Verb | Goose equivalent |
|---|---|
| `fetch`, `search` | Goose runs `integrations.scraping` (Crawl4AI) / `integrations.search` (SearXNG); Firecrawl optional fallback |
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

Point Codex at the actual checkout as its working directory, with flat native leaves under `.agents/skills/<id>/SKILL.md`. `AGENTS.md` supplies project instructions; it is not a substitute for skill placement.

### Verb bindings

| Verb | Codex tool |
|---|---|
| `read-file`, `list-files`, `grep-files` | native file tools (no config needed) |
| `write-file`, `edit-file` | native edit tools — require `--sandbox workspace-write` or higher |
| `execute-shell` | `bash -lc` tool — require `--sandbox workspace-write` or higher |
| `fetch`, `search` | `execute-shell` running `python -m integrations.scraping` (Crawl4AI) / `integrations.search` (SearXNG); Firecrawl optional fallback |
| `query-vault` | Local graph plus direct Open Knowledge search with receipt filtering |
| `vault-write` | Spotlight-local projection writer; approved and journaled only |
| `invoke-skill` | loads the named native leaf from the checkout's `.agents/skills/` |
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

Point Gemini at the actual checkout, with flat native leaves under `.gemini/skills/<id>/SKILL.md`. Supply `GEMINI.md` with the `AGENTS.md` contract; do not overwrite an existing project instruction file. Instructions alone do not replace skill discovery.

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

### Two distinct egress postures — do not conflate

`sensitive` mode and **anonymized fetch (Tor)** solve different problems:

| Posture | Trigger | Egress | Use when |
|---|---|---|---|
| **`sensitive: true`** | `AGENTS.md` frontmatter / `SPOTLIGHT_SENSITIVE=true` | **None** — `fetch`/`search` stripped, research is local-only | The *material* must not leave the machine (working with sensitive documents; a frontier model would ship context to a third party) |
| **Anonymized fetch (Tor)** | `SPOTLIGHT_ANONYMIZE_FETCH=true` (per-run) or `--tor`/`--no-tor` (per-fetch) | **Yes, via Tor** — Crawl4AI routes through `socks5://127.0.0.1:9050`; the operator's IP is hidden from the target | You *must* scrape a target-of-investigation without revealing that someone is looking (KTD8 / U7) |

They compose: an investigation can be `sensitive` (no egress) *or* anonymized (egress via Tor) *or* neither — but a `sensitive` run has no `fetch` to anonymize, so the two are rarely on together. A Tor-proxied fetch **never silently falls back to a direct (de-anonymizing) fetch** — on a Tor failure the seam raises and the operator re-runs `--no-tor` (revealing their IP) or aborts. Tor exits are widely blocklisted, so an anonymized fetch of a hard anti-bot target may simply fail — by design. The Spotlight installer provisions Tor when `SPOTLIGHT_TOR=1` (opt-in).

---

## Adding a new runtime

To add a runtime adapter doc:

1. Confirm the runtime can read `AGENTS.md` or equivalent project-context file
2. Map each of the 13 verbs to the runtime's native tools
3. Choose a sub-agent pattern (native, tmux, SDK wrapper)
4. Choose a sensitive-mode enforcement point
5. Write a new section here with the same structure as existing ones
6. Establish the exact local discovery root and execution host before adding an Engine placement route; do not infer support from an app handoff or a hypothetical extension manifest

All runtimes share the same skill content. The adapter doc is 200–400 lines of mapping and setup — the skills themselves are never rewritten per runtime.
