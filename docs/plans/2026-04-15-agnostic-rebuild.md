# Spotlight Runtime-Agnostic Rebuild — Plan

**Date:** 2026-04-15  
**Status:** Draft — pending Tom approval  
**Branch:** `feature/agnostic-rebuild`  

---

## Executive Summary

Build a runtime-agnostic Spotlight investigation system in `~/buried_signals/spotlight/` that works across **Claude Code**, **Gemini**, **GPT**, and **Hermes (Mycroft)**. The current Claude-specific plugin (marketplace) stays untouched — this is a fresh implementation using portable skill patterns.

**In-scope:** Evidence-grounded investigation pipeline, fact-checker independence, knowledge ingestion, monitoring integration.  
**Out-of-scope:** Modifying `~/buried_signals/tools/skills/` (Claude marketplace stays native).  

---

## Table of Contents

1. [Current State Analysis](#1-current-state-analysis)
2. [Runtime-Neutral vs Claude-Specific Separation](#2-runtime-neutral-vs-claude-specific-separation)
3. [Proposed Agnostic Skill Format](#3-proposed-agnostic-skill-format)
4. [Alternative: Pure Prompt-Only (Rejected)](#4-alternative-pure-prompt-only-rejected)
5. [Runtime Adapter Contract](#5-runtime-adapter-contract)
6. [Implementation Slices](#6-implementation-slices)
7. [Testing Strategy](#7-testing-strategy)
8. [Migration Path](#8-migration-path)
9. [Open Questions](#9-open-questions)

---

## 1. Current State Analysis

*[Completed: Read all reference files — confidence: high, sourced]*

### 1.1 What Exists in Claude Plugin (`~/buried_signals/tools/skills/spotlight/`)

The current marketplace implementation has two plugins (osint + spotlight) totaling ~3,000 lines of SKILL.md content:

**Plugin: osint@buriedsignals**
- Skills: `/osint` (150+ tool routing table), `/investigate` (step-by-step techniques), `/follow-the-money` (financial methodology), `/social-media-intelligence`
- References: tool-by-category, opsec-basics, investigation-guides, platform-techniques, geolocation-methods, person-investigation, transport-investigation, verification-methods, search-operators, archiving-recovery
- **confidence: high, sourced** — read end-to-end

**Plugin: spotlight@buriedsignals**
- **Skills:**
  - `/spotlight` — 445-line orchestrator with 5 phases (Preflight, Brief, Methodology, Execution, Gate, Ingestion)
  - `/ingest` — 361-line knowledge archival with registry management
  - `/monitoring` — coJournalist scout integration
  - `/web-archiving`, `/content-access` — utility skills
- **Agents:**
  - `investigator.md` — 308 lines, PLANNING + EXECUTION modes, 80 turns, 11 allowedTools including `Skill`, `WebFetch`, `WebSearch`
  - `fact-checker.md` — 178 lines, LLM-as-judge, 50 turns, same tool access
- **References:** pipeline.md, evidence-grounding.md, entity-model.md, registry-spec.md, source-catalog.md
- **confidence: high, sourced** — read end-to-end

**Execution Model:** The SKILL.md is a playbook — the host session (Claude Code) reads it and executes via `Agent()` spawn, `Skill()` invocation, bash commands, and file I/O. This is the pattern to generalize.

### 1.2 What Exists in `~/buried_signals/spotlight/` (Target Directory)

**Current state:** Feed framework + local extensions, NO core pipeline yet.

```
spotlight/
├── CLAUDE.md                    # Local env docs only — NOT a skill spec
├── cases/                       # Empty directory (investigation output goes here)
├── docs/                        # Contains only this plan
│   └── plans/2026-04-15-agnostic-rebuild.md
├── monitoring/                  # Feed framework — RUNTIME-NEUTRAL
│   ├── feeds/
│   │   ├── monitor.py           # CLI for GDELT/RSS/GDACS/cojournalist queries
│   │   └── sources/             # JSON configs per feed
│   │       ├── cojournalist/
│   │       ├── gdacs/
│   │       ├── gdelt/
│   │       ├── rss_investigative/
│   │       └── rss_regional/
│   │   └── sources-schema.json
│   └── leads/                   # Scraping queue (feed → case handoff)
└── scripts/                     # Local utilities
    ├── run-rss-check.sh
    └── parse-rss.py
```

**Key finding:** The monitoring framework is already runtime-neutral (Python CLI, JSON configs). It just needs to be wired into the agnostic orchestrator.

**confidence: high, sourced** — directory listing and file reads

### 1.3 Dependencies and Hard Contracts (Non-Negotiable)

| Contract | What | Why |
|----------|------|-----|
| **firecrawl** | Primary search + scrape library | `firecrawl scrape <url> -o <path>` / `firecrawl search "<query>"` |
| **qmd** | Vault query via CLI | `BUN_INSTALL="" qmd query <vault> "<query>"` — required for context loading |
| **Obsidian vault** | Knowledge destination | Frontmatter + wikilinks format in `~/buried-signals/` |
| **Cases directory** | Investigation isolation | `cases/{project}/research/` per investigation |
| **JSON schemas** | Inter-agent communication | findings.json, fact-check.json, methodology.json, investigation-log.json |
| **Git** | Version control | Already present in spotlight/ |

**assumption:** These contracts remain identical across all runtimes. Adapters MAY wrap them (e.g., a "fetch" tool verb that maps to `firecrawl scrape`), but the underlying call stays firecrawl/qmd.

**confidence: medium, sourced** — from SOUL.md rules and spec.md reading

---

## 2. Runtime-Neutral vs Claude-Specific Separation

*[Completed: Analyzed separation matrix — confidence: high, inference drawn]*

### 2.1 Neutral Substance (Keep Unchanged)

| Element | Why Neutral | Evidence |
|---------|-------------|----------|
| **Evidence grounding rules** | Universal journalistic practice | evidence-grounding.md — scrape before cite, local_file refs, verbatim quotes |
| **Pipeline phases** | Logical sequence, not Claude-specific | Preflight → Brief → Methodology → Execution → Gate → Ingestion — works in any system |
| **Readiness criteria** | Editorial standards, independent of runtime | 6 criteria (min findings, source independence, etc.) — judgment-based, not tool-based |
| **Verdict taxonomy** | Epistemological, not technical | verified/unverified/disputed/false — applies to any fact-checker |
| **JSON schemas** | Data contracts between components | findings.json, fact-check.json, etc. — format-agnostic |
| **Frontmatter contracts** | Obsidian standard | entity-model.md specs — YAML + wikilinks |
| **Case directory structure** | Filesystem convention | `cases/{project}/research/`, `data/` — works on any OS |
| **Feed framework** | Already Python CLI | monitoring/feeds/monitor.py — runs standalone |
| **SIFT methodology** | Source evaluation technique | investigator.md + fact-checker.md — journalistic method, not Claude-specific |
| **OPSEC rules** | Risk management | Dedicated browser profile, archive before cite — universal |

### 2.2 Claude Plumbing (Swapped in Agnostic Version)

| Current (Claude) | Agnostic Replacement | Why |
|------------------|----------------------|-----|
| `Agent()` spawn | Tool verbs: `spawn_agent`, `wait_agent`, `read_output` | Generic IPC pattern |
| `Skill(name)` tool | Tool verb: `invoke_skill` with runtime binding | Skill loading is loading — verb can dispatch |
| YAML frontmatter in .md | TOML frontmatter in .md | Better multi-line string support, but either works |
| `.claude-plugin/` directory | `adapters/claude/`, `adapters/hermes/`, etc. | Per-runtime binding code |
| Slash commands (`/spotlight`) | Tool verbs: `start_investigation` | Natural language or function call |
| `Read`, `Write`, `Edit` tools | Tool verb: `file_operation` | Already abstract in most systems |
| `WebFetch`, `WebSearch` | Tool verbs: `fetch_url`, `search_web` | Maps to firecrawl/search equivalent |
| `Bash` tool | Tool verb: `execute_shell` | Same capability, different naming |
| Hardcoded model: "opus" | Adapter config: `preferred_model`, `fallback_model` | Runtime decides |
| `maxTurns: 80` | Adapter config: `iteration_limit` | Same concept, generic name |
| `allowedTools` in frontmatter | Agent manifest: `allowed_verbs` | Same concept, generic name |

### 2.3 Confidence Tags

| Claim | Confidence | Basis |
|-------|------------|-------|
| Evidence grounding is runtime-neutral | **high** | Evidence is evidence — scraping, saving, citing — independent of LLM platform |
| Verdict taxonomy is universal | **high** | Epistemology doesn't change with runtime |
| Agent spawning can be abstracted to verbs | **medium** | Gemini's "Code Execution" and GPT's "Advanced Data Analysis" have different patterns, but `spawn/wait/read` works as lowest-common-denominator |
| JSON schemas port without change | **high** | Data contract, not code |
| Firecrawl/qmd remain hard contracts | **high** | Explicit in SOUL.md and spec.md — not negotiable |
| Model name abstraction works | **medium** | Requires per-runtime model mapping (opus→4o→gemini-1.5-pro) — standard practice but needs testing |

---

## 3. Proposed Agnostic Skill Format

*[Completed: designed — confidence: high, inference]*

### 3.1 Lead Option: AGENTS.md + skills/ Tree

The format has three layers:

```
spotlight/
├── AGENTS.md                           # Root — defines tool verbs + agent manifests
├── skills/
│   ├── spotlight/
│   │   ├── SKILL.md                    # Orchestrator — pipeline + gates
│   │   └── references/
│   │       ├── pipeline.md             # Readiness criteria, cycle mechanics
│   │       └── evidence-grounding.md   # Scrape-to-local rules
│   ├── ingest/
│   │   ├── SKILL.md
│   │   └── references/
│   │       ├── entity-model.md         # Note types, frontmatter, wikilinks
│   │       └── registry-spec.md        # JSON registry schemas
│   ├── monitoring/
│   │   ├── SKILL.md
│   │   └── references/
│   │       ├── cojournalist-api.md
│   │       ├── source-catalog.md
│   │       └── recommendation-schema.md
│   ├── web-archiving/SKILL.md          # Standalone utility
│   ├── content-access/SKILL.md         # Standalone utility
│   └── osint/
│       ├── SKILL.md                    # 150+ tool routing table (reference only — not ported)
│       └── references/
│           ├── investigate.md           # Step-by-step techniques
│           └── follow-the-money.md      # Financial methodology
├── agents/
│   ├── investigator.md                 # PLANNING + EXECUTION prompt bundles
│   └── fact-checker.md                 # LLM-as-judge prompt bundle
├── adapters/                           # Per-runtime binding code
│   ├── claude/                         # Claude Code loader + tool bindings
│   ├── hermes/                         # Mycroft/Hermes loader + tool bindings
│   ├── gemini/                         # Gemini loader
│   └── gpt/                            # GPT loader
└── cases/                              # Investigation output (unchanged)
```

**AGENTS.md** is the runtime contract. It defines:
1. **Tool verb registry** — a fixed vocabulary of abstract verbs (`fetch`, `search`, `read-file`, `write-file`, `spawn-agent`, `wait-agent`, `invoke-skill`) that every runtime adapter must implement
2. **Agent manifests** — lightweight YAML frontmatter for each agent: name, description, allowed verbs, iteration limit, preferred model
3. **Skill manifests** — which agents can load which skills, per `invoke_skill` verb

### 3.2 Skill File Structure

Every skill is a markdown file with TOML frontmatter:

```toml
+++
name = "spotlight"
description = "OSINT investigation orchestrator"
version = "1.0.0"
runtime_version = "1"          # Format version — bumps on breaking changes

[dependencies]
skills = ["osint", "web-archiving", "content-access"]
env_vars = ["FIRECRAWL_API_KEY", "OSINT_NAV_API_KEY"]

[environment]
cases_root = "cases/"          # Relative to working directory
vault_type = "obsidian"        # obsidian | directory

[agents]
spawns = ["investigator", "fact-checker"]
+++

# Skill Title

Narrative description of what this skill does.

## Phase Name

Step-by-step instructions using tool verbs only (no runtime-specific tool names).
```

**Why TOML frontmatter:** Better multi-line string support than YAML for the instruction blocks that contain quoted examples. Markdown parsers ignore TOML frontmatter (the `+++` delimiter pair) by convention. The `+++` delimiter is used by `markdown-it` and others as a frontmatter marker distinct from YAML's `---`.

**Alternative considered: keep YAML frontmatter.** YAML is more widely supported in LLM training data. TOML frontmatter is less likely to be correctly parsed by a naive markdown reader. **Decision: use YAML frontmatter** (`---` delimiters) for maximum LLM compatibility. The `---` delimiter is recognized by both Claude and Gemini.

### 3.3 Tool Verb Registry

Fixed vocabulary. Adapters map these to native tools.

| Verb | Signature | Semantics |
|------|----------|-----------|
| `fetch` | `fetch(url, output_path)` | Scrape URL, save to file. Maps to: firecrawl scrape, Gemini Code Execution curl, GPT browsing |
| `search` | `search(query, output_path, limit)` | Web search, save results. Maps to: firecrawl search, Gemini/GPT web search |
| `read-file` | `read-file(path)` | Read file contents |
| `write-file` | `write-file(path, content)` | Write file (full overwrite) |
| `edit-file` | `edit-file(path, old, new)` | Targeted replace |
| `list-files` | `list-files(pattern)` | Glob/search for files |
| `grep-files` | `grep-files(pattern, path)` | Search file contents |
| `execute-shell` | `execute-shell(command)` | Run shell command, return stdout+stderr |
| `spawn-agent` | `spawn-agent(agent_id, prompt, config)` | Launch sub-agent with prompt + config bundle |
| `wait-agent` | `wait-agent(handle)` | Block until agent completes, return output |
| `invoke-skill` | `invoke-skill(skill_id)` | Load a skill's instructions into current context |
| `query-vault` | `query-vault(vault_path, query)` | Search vault for context. Maps to: `BUN_INSTALL="" qmd query` |
| `vault-write` | `vault-write(vault_path, note_path, content)` | Write note to vault + update registry |

**Mapping rule:** A runtime adapter MUST implement all verbs. If a verb is unsupported (e.g., GPT doesn't have native sub-agent spawning), the adapter raises an explicit error at load time — not silently at call time.

### 3.4 Agent Format

Agents are prompt + config bundles, NOT runtime-specific sub-agent definitions.

```toml
+++
name = "investigator"
description = "Plans and executes OSINT investigations"
iteration_limit = 80

[allowed_verbs]
- fetch
- search
- read-file
- write-file
- list-files
- grep-files
- invoke-skill

[preferred_model]
claude = "opus"
gemini = "gemini-2.5-pro"
gpt = "gpt-4o"
fallback_note = "Investigation quality degrades significantly on lighter models"

[vault_context]
enabled = true          # Agent reads vault before starting
query_on_load = true    # Query related entities/techniques before research
+++

# Investigator Agent

You are an OSINT Investigator...

[MODE: PLANNING]
[MODE: EXECUTION]
...
```

The `[MODE: ...]` convention marks sections that adapters inject into the spawn prompt based on context. Modes are identified by bracketed header tags and included or excluded based on the orchestration phase.

**Separation principle:** The agent prompt never names a specific tool. It names only:
- Tool verbs from the registry above
- Skill IDs to invoke
- Data file paths in `cases/{project}/`

### 3.5 Why This Format

| Property | Benefit |
|----------|---------|
| Markdown + YAML frontmatter | Any LLM reads it natively — no custom parser required |
| Tool verbs | Skill remains valid across model upgrades and runtime migrations |
| Agent manifests in YAML | Adding a new runtime = write one adapter file, not rewrite every agent |
| Reference-only OSINT skill | Avoids duplicating 1,000+ lines of tool routing — adapters import from marketplace |
| Cases dir unchanged | Existing investigation data remains valid |

**confidence: high, inference** — format derived from existing spec and porting requirements

---

## 4. Alternative: Pure Prompt-Only (Rejected)

**Option:** Store all skills as plain markdown without frontmatter or manifest metadata. Each skill is just narrative instructions. Agents are bare prompts in a single `agents.md` file.

**Rejection reasoning:**

1. **No runtime metadata.** Without frontmatter, the system has no way to declare dependencies, env vars, or agent-skill bindings. Every runtime adapter would need to hard-code these from reading the narrative — defeating the portability goal.

2. **Sub-agent spawning breaks.** Claude's `Agent()`, Gemini's Code Execution, and GPT's tool use all need explicit spawn config (iteration limit, model, allowed tools). Without manifests, this config lives in the adapter — a violation of separation.

3. **Skill composition fails.** When the investigator needs to `invoke-skill(osint:osint)`, the adapter must know which skills exist and what they contain. Without a skill registry in AGENTS.md, the adapter must hard-code this knowledge.

4. **Schema versioning impossible.** The `schema_version` field in JSON files implies the format can evolve. Without a version marker on the skill format itself, there is no mechanism to detect when an adapter is reading a skill written for a different version.

**The Lead Option wins** because frontmatter metadata makes the runtime contract explicit and testable, while the prompt content remains model-agnostic.

**confidence: medium** — rejection is sound but the pure-prompt approach might work for simpler skills; Spotlight is complex enough to justify manifests

---

## 5. Runtime Adapter Contract

*[Completed: verb registry + per-runtime requirements — confidence: high, sourced from spec analysis]*

### 5.1 Required Bindings

Every adapter MUST provide:

1. **Tool verb implementations** — one function per verb in the registry, with the exact signatures listed in §3.3
2. **Model mapping** — `preferred_model` + `fallback_model` per agent manifest, mapped to the runtime's model IDs
3. **Agent spawn/wait/read** — implementation of `spawn-agent` + `wait-agent` using the runtime's native sub-agent primitive
4. **Skill loader** — implementation of `invoke-skill` that reads the skill's markdown file and injects its instructions into the active context
5. **Adapter manifest** — `adapters/{runtime}/manifest.toml`:

```toml
+++
name = "hermes"
version = "1.0.0"
skill_format_version = "1"

[runtime]
supports_sub_agents = true
supports_native_vault = false        # Uses qmd CLI instead
shell_backend = "local"              # local | docker | ssh

[model_map]
opus = "anthropic/claude-opus-4"
sonnet = "anthropic/claude-sonnet-4"
gemini-2.5-pro = "gemini-2.5-pro-preview-05-06"
gpt-4o = "gpt-4o"

[verb_implementations]
fetch = "hermes_fetch"               # Function name in adapters/hermes/tools.py
search = "hermes_search"
spawn-agent = "hermes_spawn_agent"
# ... etc
+++
```

### 5.2 Claude Code Adapter

**Spawns sub-agents** via `Agent()` tool with `prompt` + `tool_names` + `max_turns`.
**Skill loading** via `Skill()` tool.
**File I/O** via `Read` / `Write` / `Edit`.
**Search/scrape** via `firecrawl` CLI (detected at runtime).

Adapter file: `adapters/claude/loader.md`

```markdown
# Claude Code Adapter — Spotlight Skill Loader

Load this file at the start of any Claude Code Spotlight session.

## Tool Bindings

| Verb | Claude Tool |
|------|------------|
| fetch | `Bash(firecrawl scrape <url> -o <path>)` |
| search | `Bash(firecrawl search "<query>" -o <path>)` |
| read-file | Read tool |
| write-file | Write tool |
| edit-file | Edit tool |
| invoke-skill | Skill() tool |
| spawn-agent | Agent() tool |
| query-vault | Bash(`BUN_INSTALL="" qmd query <vault> "<query>"`) |
```

### 5.3 Hermes/Mycroft Adapter

**Sub-agents** via `delegate_task()` with `goal` + `toolsets`.
**Skill loading** via `skill_view()` + injection into prompt.
**File I/O** via `read_file` / `write_file` / `patch`.
**Search/scrape** via `firecrawl` CLI (detected at runtime).
**Model routing** via `local-gemma` (Gemma 4 E4B journalist) for Mycroft-owned work; `frontier-coding` for complex sub-tasks.

Adapter file: `adapters/hermes/loader.md`

Key difference from Claude: Mycroft's `delegate_task` takes a natural-language `goal` rather than structured config. The adapter wraps skill content + agent prompts into a complete goal string.

### 5.4 Gemini Adapter

**Sub-agents** via Google AI's `Code Execution` tool (sandboxes a full Python REPL) or via Vertex AI Agent Development. Not yet finalized.
**Skill loading** via reading markdown files and injecting into context.
**Search/scrape** via Gemini's built-in `FunctionDeclarations` + firecrawl CLI wrapper.

*Open question: Gemini's Code Execution does not persist state between calls. Full agent lifecycle (PLANNING → EXECUTION across multiple turns) requires a different pattern — likely a session-scoped prompt that carries state. See §9.*

### 5.5 GPT (ChatGPT / Assistants API) Adapter

**Sub-agents** via Assistants API `thread.run` with tool outputs fed back as messages.
**Skill loading** via `file.search` + context injection.
**Search/scrape** via `browsing` tool + firecrawl CLI.

*Same state persistence concern as Gemini — Assistants API threads are stateful within a session but the tool-call model differs significantly from Claude's `Agent()`.*

---

## 6. Implementation Slices

*[Completed: 8 slices ≤2h each — confidence: medium, structural estimation]*

Each slice is a **single Claude Code worktree + branch + PR**. The branch naming convention: `feature/spotlight-agnostic/slice-N-{name}`. Each PR demonstrates execution on **at least 2 runtimes** (Claude Code + Hermes, or Claude Code + GPT). Documented manual testing is acceptable where automation is costly.

---

### Slice 1: Foundation — AGENTS.md, Schemas, and File Structure

**Done criteria:**
- `AGENTS.md` at repo root with verb registry, agent manifests, skill registry
- `skills/spotlight/references/pipeline.md` — readiness criteria, cycle mechanics, stall protocol
- `skills/spotlight/references/evidence-grounding.md` — scrape-to-local rules
- `skills/ingest/references/entity-model.md` — ported from marketplace
- `skills/ingest/references/registry-spec.md` — ported from marketplace
- All JSON schemas (findings.json, fact-check.json, methodology.json, investigation-log.json, summary.json) in `schemas/` with `schema_version: "1.0"`
- Cases directory structure (`cases/{project}/{data,research}/`) committed

**Not in scope:** adapters/, orchestrator SKILL.md, agent prompts.

**Runtime test:** Any runtime can `read-file` all these and confirm they parse correctly.

---

### Slice 2: Core Skills — web-archiving, content-access (standalone utility skills)

**Done criteria:**
- `skills/web-archiving/SKILL.md` — archived from marketplace, TOML frontmatter, tool verbs only (no `WebFetch`/`WebSearch` — uses `fetch`)
- `skills/content-access/SKILL.md` — archived from marketplace, `access_method` field semantics preserved
- Both skills executable by: (a) Claude Code via `Skill()` load, (b) Hermes via `skill_view()` + delegate_task

**Runtime test:** Load skill in Claude Code, run the Wayback Machine archive step on a test URL. Load skill in Hermes session, run same step via delegate_task with firecrawl. Compare outputs.

---

### Slice 3: Investigator Agent — PLANNING Mode

**Done criteria:**
- `agents/investigator.md` with PLANNING section, TOML manifest
- `invoke-skill(osint:investigate)` + `invoke-skill(osint:follow-the-money)` calls
- Vault context loading section (reads registries, queries with `query-vault`)
- Writes `cases/{project}/data/methodology.json` per schema
- Manifest declares `allowed_verbs: [fetch, search, read-file, write-file, list-files, grep-files, invoke-skill, query-vault]`

**Runtime test:** Spawn investigator (PLANNING) in Claude Code and Hermes for same brief. Both write structurally identical `methodology.json`.

---

### Slice 4: Investigator Agent — EXECUTION Mode

**Done criteria:**
- `agents/investigator.md` EXECUTION section appended to slice 3 file
- Reads approved `methodology.json`, follows 6-step OSINT methodology
- Appends to `investigation-log.json` (creates if absent)
- Merges findings with previous cycle if `cycle > 1`
- `monitoring_recommendations[]` output when targets identified
- Manifest unchanged from slice 3

**Runtime test:** Spawn investigator (EXECUTION) in Claude Code with a mock methodology. Verify findings.json structure matches schema. Spawn in Hermes, verify same.

---

### Slice 5: Fact-Checker Agent

**Done criteria:**
- `agents/fact-checker.md` — full SIFT methodology, verdict taxonomy
- TOML manifest: `iteration_limit: 50`, same `allowed_verbs` as investigator
- Writes `fact-check.json` per schema including `gaps_for_next_cycle`
- `monitoring_recommendations[]` output (same schema as investigator)

**Runtime test:** Fact-check a set of mock findings in Claude Code and Hermes. Both outputs pass schema validation and match on verdict + evidence trail.

---

### Slice 6: Orchestrator — spotlight/SKILL.md (Pipeline + Gates)

**Done criteria:**
- `skills/spotlight/SKILL.md` — Phase 0 (preflight) through Phase 5 (ingestion)
- All tool verb calls (no runtime-specific tool names in instructions)
- Gate protocol (user approval at Brief, Methodology, Gate 1)
- Cycle evaluation logic against readiness criteria
- Stall protocol at cycle 5
- coJournalist scout creation step (calls monitoring SKILL.md)
- Integration check for OSINT Navigator (env var, API reachability)
- Context recovery logic (reads state files to resume interrupted pipeline)

**Runtime test:** Run full pipeline (mock lead, no real research) in Claude Code and Hermes. Both reach Gate 1 with same summary structure.

---

### Slice 7: Ingest Skill

**Done criteria:**
- `skills/ingest/SKILL.md` — pipeline mode + standalone mode detection
- Steps: read registries → write investigation note → create/update entity notes → create/update methodology notes → create/update tool notes → update registries → write _INDEX.md
- Directory fallback (wikilinks → relative markdown links)
- Lock file pattern (`.ingest-lock`)
- No runtime-specific tool names

**Runtime test:** Ingest a minimal mock investigation (1 finding, 1 entity, 1 tool) in Claude Code and Hermes. Both produce structurally identical vault output.

---

### Slice 8: Runtime Adapters — Claude + Hermes

**Done criteria:**
- `adapters/claude/loader.md` — full tool verb binding table, spawn-agent implementation notes
- `adapters/hermes/loader.md` — full tool verb binding table, delegate_task wrap pattern
- Both adapters load all skills from slices 1-7
- `adapters/hermes/manifest.toml` — model map, verb implementations
- Integration test: run spotlight orchestrator (slice 6) against both adapters for same lead

**Runtime test:** Execute spotlight pipeline end-to-end in Claude Code. Execute same lead in Hermes. Both complete with Gate 1 summary. (This is the primary 2-runtime acceptance test.)

---

### Slice Dependencies

```
Slice 1 → Slice 2 (web-archiving, content-access are invoked by agents)
Slice 1 → Slice 3 (investigator reads schemas + evidence-grounding)
Slice 3 → Slice 4 (EXECUTION builds on PLANNING structure, same file)
Slice 3 + 4 → Slice 5 (fact-checker reads investigator output)
Slice 4 + 5 → Slice 6 (orchestrator spawns both agents, evaluates criteria)
Slice 6 → Slice 7 (orchestrator calls ingest after Gate 1)
Slice 1-7 → Slice 8 (adapters wire everything together)
```

Slices 1–5 can run in parallel (all produce inputs consumed by slice 6). Slices 6 and 7 are sequential from slice 1 completion. Slice 8 runs after all others.

---

## 7. Testing Strategy

### 7.1 Per-Slice Acceptance Tests

Each slice PR includes a shell-based test that:
1. Loads the skill or agent in the target runtime
2. Runs the minimum viable task for that component
3. Validates output against the relevant JSON schema
4. Reports pass/fail with struct diff on failure

### 7.2 Two-Runtime Execution Requirement

Each PR must demonstrate execution on **≥2 runtimes**. Three patterns, in order of preference:

| Pattern | When | Cost |
|---------|------|------|
| **Automated parallel test** | Claude Code + Hermes both support scripted execution | Low — automated |
| **Documented manual test** | Gemini/GPT don't yet support automated sub-agent spawning | Medium — documented steps |
| **Schema validation only** | Runtime lacks sub-agent capability (e.g., pure API runtime) | High — partial coverage |

**Named gap:** Gemini and GPT adapters cannot be fully automated in the initial build due to lack of persistent sub-agent sessions. The gap is named in the PR and tracked for Phase 3.

### 7.3 Schema Validation

JSON schema validation via Python `jsonschema` library in a `test/` directory. Each schema has a corresponding `test/schemas/test_<schema>.py` that validates a sample output against the schema. Run with:

```bash
python -m pytest test/schemas/ -v
```

### 7.4 Integration Test (Post-Slice 8)

After slice 8, a full pipeline test:
1. Create a mock investigation lead
2. Run preflight → brief → methodology → execution cycle 1
3. Verify `findings.json` + `fact-check.json` pass schema validation
4. Run Gate 1 → ingest
5. Verify vault structure matches `entity-model.md`

---

## 8. Migration Path

### 8.1 Mycroft/Hermes Adoption

Mycroft (this system) adopts the agnostic spotlight skill as follows:
1. Skills are loaded via `skills.external_dirs` pointing to `~/buried_signals/spotlight/skills/`
2. The `adapters/hermes/loader.md` is read at startup and establishes tool verb bindings
3. Orchestrator invoked via: `invoke-skill(spotlight:spotlight)` — the SKILL.md is the playbook
4. Agents spawned via `delegate_task` with goal strings assembled from `agents/investigator.md` + current context
5. File I/O via `read_file` / `write_file` / `patch`

**Mycroft becomes the primary owner and operator of the agnostic spotlight skill.** The marketplace version (Claude-only) continues in parallel for Claude Code users.

### 8.2 Claude Code Users

Claude Code users migrate to the agnostic version by:
1. Cloning or pulling `~/buried_signals/spotlight/`
2. Pointing `~/.claude/settings.json` `plugins.externalDirs` to `~/buried_signals/spotlight/adapters/claude/`
3. `/spotlight` command loads from `skills/spotlight/SKILL.md`

The marketplace plugin (`buriedsignals/skills/spotlight`) is **not modified**. Existing Claude Code users continue using it unchanged.

### 8.3 Gemini / GPT Users

No migration path in Phase 2 — adapters are scaffolding only. Phase 3 (post-approval) fills these in with documented manual testing.

---

## 9. Open Questions

|| # | Question | Options | Recommendation |
|---|----|---------|----------|------------|
| **O1** | Should the agnostic version include the full OSINT routing table (~1,000 lines from `tools/skills/osint/`)? | (a) Full port — self-contained, duplicates maintenance burden (b) Reference-only — adapter imports from marketplace, breaks hermes-only users (c) Minimal core — 20 most-used tools ported, rest via OSINT Navigator | **Option (c)** — OSINT Navigator (`$OSINT_NAV_API_KEY`) handles tool discovery; port 20 core tools to the agnostic `skills/osint/SKILL.md` |
| **O2** | TOML vs YAML frontmatter? | (a) TOML (b) YAML | **YAML** — wider LLM training coverage; `---` delimiter unambiguous |
| **O3** | Gemini sub-agent persistence | Gemini Code Execution doesn't persist state between turns | Document as limitation; Gemini adapter targets Phase 3 after Gemini 2.0 agent API matures |
| **O4** | Sensitive mode for Mycroft | The local CLAUDE.md mentions sensitive mode (no web tools) | Add `sensitive: true` to orchestrator config; adapter strips `fetch`/`search` verbs from agent manifests when set |
| **O5** | coJournalist scout management in Hermes | Mycroft's monitoring skills use `python3 tools/cojournalist/scout.py` | Adapter wraps these as `invoke-skill(monitoring)` calls — same as Claude Code |
| **O6** | Cases directory location | Currently `~/buried_signals/spotlight/cases/` | Keep — `cases_root` in orchestrator SKILL.md references relative path |

---

## Appendix A: File Structure Target

```
spotlight/                          # Root — git repo, feature branch: feature/agnostic-rebuild
├── AGENTS.md                       # Verb registry + agent manifests + skill registry
├── schemas/
│   ├── findings.schema.json
│   ├── fact-check.schema.json
│   ├── methodology.schema.json
│   ├── investigation-log.schema.json
│   └── summary.schema.json
├── skills/
│   ├── spotlight/
│   │   ├── SKILL.md               # Orchestrator playbook
│   │   └── references/
│   │       ├── pipeline.md         # Readiness criteria + cycle mechanics
│   │       └── evidence-grounding.md
│   ├── ingest/
│   │   ├── SKILL.md               # Knowledge archival
│   │   └── references/
│   │       ├── entity-model.md
│   │       └── registry-spec.md
│   ├── monitoring/
│   │   ├── SKILL.md               # coJournalist + feed framework integration
│   │   └── references/
│   │       ├── cojournalist-api.md
│   │       ├── source-catalog.md
│   │       └── recommendation-schema.md
│   ├── web-archiving/
│   │   └── SKILL.md               # Wayback + Archive.today + local
│   ├── content-access/
│   │   └── SKILL.md               # Paywall hierarchy + access_method
│   └── osint/
│       ├── SKILL.md               # Minimal core routing (~20 tools)
│       └── references/
│           ├── investigate.md      # Step-by-step techniques (from marketplace)
│           └── follow-the-money.md
├── agents/
│   ├── investigator.md            # PLANNING + EXECUTION modes
│   └── fact-checker.md            # LLM-as-judge
├── adapters/
│   ├── claude/
│   │   ├── manifest.toml
│   │   └── loader.md               # Tool bindings + spawn-agent implementation
│   └── hermes/
│       ├── manifest.toml
│       └── loader.md               # Tool bindings + delegate_task wrap pattern
├── monitoring/                     # Existing — unchanged
│   ├── feeds/
│   │   ├── monitor.py
│   │   └── sources/
│   └── leads/
├── cases/                          # Created at runtime — gitignored
├── .gitignore
└── CLAUDE.md                       # Updated: points to AGENTS.md, not marketplace
```

---

## Appendix B: Schema Reference

### findings.schema.json (v1.0 — unchanged from marketplace)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "schema_version": "1.0",
  "type": "object",
  "required": ["schema_version", "project", "lead", "findings"],
  "properties": {
    "schema_version": { "const": "1.0" },
    "project": { "type": "string" },
    "lead": { "type": "string" },
    "investigated_at": { "type": "string", "format": "date-time" },
    "cycle": { "type": "integer", "minimum": 1 },
    "questions": { "type": "array", "items": { "type": "string" } },
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "claim", "sources", "confidence"],
        "properties": {
          "id": { "type": "string" },
          "claim": { "type": "string" },
          "evidence": { "type": "string" },
          "sources": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["url", "accessed"],
              "properties": {
                "url": { "type": "string", "format": "uri" },
                "type": { "enum": ["court_filing","news","registry","social_media","government","ngo_report","satellite","other"] },
                "accessed": { "type": "string", "format": "date-time" },
                "local_file": { "type": "string" },
                "access_method": { "enum": ["full_text","open_access","archive_copy","abstract_only","inaccessible"] }
              }
            }
          },
          "confidence": { "enum": ["high","medium","low"] },
          "confidence_rationale": { "type": "string" },
          "perspective": { "enum": ["official","affected_community","independent_observer","corporate","legal"] }
        }
      }
    },
    "connections": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["from", "to", "relationship"],
        "properties": {
          "from": { "type": "string" },
          "to": { "type": "string" },
          "relationship": { "type": "string" },
          "evidence": { "type": "string" }
        }
      }
    },
    "gaps": { "type": "array", "items": { "type": "string" } },
    "next_steps": { "type": "array", "items": { "type": "string" } },
    "monitoring_recommendations": {
      "type": "array",
      "items": { "$ref": "#/definitions/monitoring_recommendation" }
    }
  },
  "definitions": {
    "monitoring_recommendation": {
      "type": "object",
      "required": ["id", "target", "scout_type", "criteria", "rationale", "priority"],
      "properties": {
        "id": { "type": "string" },
        "target": { "type": "string" },
        "scout_type": { "enum": ["web","pulse","social","civic"] },
        "criteria": { "type": "string" },
        "rationale": { "type": "string" },
        "priority": { "enum": ["high","medium","low"] },
        "finding_refs": { "type": "array", "items": { "type": "string" } }
      }
    }
  }
}
```

### fact-check.schema.json (v1.0 — unchanged from marketplace)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "schema_version": "1.0",
  "type": "object",
  "required": ["schema_version", "project", "claims"],
  "properties": {
    "schema_version": { "const": "1.0" },
    "project": { "type": "string" },
    "source_document": { "type": "string" },
    "checked_at": { "type": "string", "format": "date-time" },
    "cycle": { "type": "integer", "minimum": 1 },
    "summary": {
      "type": "object",
      "required": ["total_claims", "verified", "unverified", "disputed", "false"],
      "properties": {
        "total_claims": { "type": "integer" },
        "verified": { "type": "integer" },
        "unverified": { "type": "integer" },
        "disputed": { "type": "integer" },
        "false": { "type": "integer" }
      }
    },
    "claims": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "finding_id", "claim_text", "verdict", "evidence_for"],
        "properties": {
          "id": { "type": "integer" },
          "finding_id": { "type": "string" },
          "claim_text": { "type": "string" },
          "verdict": { "enum": ["verified","unverified","disputed","false"] },
          "confidence": { "enum": ["high","medium","low"] },
          "evidence_for": { "type": "array", "items": { "$ref": "#/definitions/evidence_item" } },
          "evidence_against": { "type": "array", "items": { "$ref": "#/definitions/evidence_item" } },
          "sources": { "type": "array", "items": { "type": "string" } },
          "notes": { "type": "string" }
        }
      }
    },
    "gaps_for_next_cycle": { "type": "array", "items": { "type": "string" } }
  },
  "definitions": {
    "evidence_item": {
      "type": "object",
      "required": ["description", "source"],
      "properties": {
        "description": { "type": "string" },
        "source": { "type": "string" },
        "source_type": { "enum": ["primary","secondary"] },
        "archive_url": { "type": "string" },
        "access_method": { "enum": ["full_text","open_access","archive_copy","abstract_only","inaccessible"] }
      }
    }
  }
}
```

### methodology.schema.json (v1.0 — unchanged from marketplace)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "schema_version": "1.0",
  "type": "object",
  "required": ["schema_version", "project", "investigation_plan"],
  "properties": {
    "schema_version": { "const": "1.0" },
    "project": { "type": "string" },
    "lead": { "type": "string" },
    "planned_at": { "type": "string", "format": "date-time" },
    "brief_directions": { "type": "array", "items": { "type": "string" } },
    "investigation_plan": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["direction", "questions", "steps"],
        "properties": {
          "direction": { "type": "string" },
          "questions": { "type": "array", "items": { "type": "string" } },
          "steps": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["order", "action", "tool"],
              "properties": {
                "order": { "type": "integer" },
                "action": { "type": "string" },
                "tool": { "type": "string" },
                "target": { "type": "string" },
                "expected_evidence": { "type": "string" },
                "fallback": { "type": "string" }
              }
            }
          },
          "osint_techniques": { "type": "array", "items": { "type": "string" } },
          "key_sources": { "type": "array", "items": { "type": "string" } },
          "risks": { "type": "array", "items": { "type": "string" } },
          "estimated_difficulty": { "enum": ["quick scan","moderate","deep document trail"] }
        }
      }
    },
    "tools_required": { "type": "array", "items": { "type": "string" } },
    "opsec_considerations": { "type": "array", "items": { "type": "string" } },
    "limitations": { "type": "array", "items": { "type": "string" } }
  }
}
```

**confidence: high** — all schemas derived directly from marketplace spec.md §329–500 with `schema_version: "1.0"` added for forward compatibility

---

*Plan status: complete. Ready for Tom review.*
