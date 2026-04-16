---
name: runtime-adapters
description: Runtime-agnostic adapter implementations for the Spotlight OSINT system
version: "1.0"
runtime_version: "1"
---

# Spotlight — Runtime Adapters

Runtime adapters implement the tool verb registry defined in `AGENTS.md` for specific execution environments. Each adapter translates abstract verb operations into runtime-specific implementations.

## Adapter Architecture

```
                    ┌─────────────────────────────────────────┐
                    │              AGENTS.md                  │
                    │     (Tool Verb Registry Contract)       │
                    └─────────────────┬───────────────────────┘
                                      │
                    ┌─────────────────┴───────────────────────┐
                    │         Runtime Adapter Layer          │
                    ├─────────────────┬─────────────────────┤
                    │                 │                     │
              ┌─────▼─────┐     ┌──────▼──────┐      ┌──────▼──────┐
              │  Claude   │     │   Hermes    │      │  Future     │
              │  Adapter  │     │   Adapter   │      │  Adapters   │
              └─────┬─────┘     └──────┬──────┘      └─────────────┘
                    │                 │
         ┌──────────┴─────────────────┴──────────┐
         │       Runtime Environment              │
         │  (Claude Code, Hermes Agent, etc.)     │
         └───────────────────────────────────────┘
```

## Adapter Contract

Every adapter MUST implement all verbs from the registry. At load time, the adapter validates that all required verbs are implemented. If any verb is missing, the adapter raises `AdapterValidationError` with a list of missing verbs.

### Required Verbs

| Verb | Implementation Required |
|------|-------------------------|
| `fetch` | Scrape URL content, save to file |
| `search` | Web search, save results to file |
| `read-file` | Read file contents |
| `write-file` | Write file (full overwrite) |
| `edit-file` | Targeted string replacement |
| `list-files` | Glob/search for files matching pattern |
| `grep-files` | Search file contents by regex |
| `execute-shell` | Run shell command, return stdout + stderr |
| `spawn-agent` | Launch sub-agent with prompt and config |
| `wait-agent` | Block until agent completes, return output |
| `invoke-skill` | Load skill instructions into current context |
| `query-vault` | Search knowledge vault for context |
| `vault-write` | Write note to vault and update registry |

## Available Adapter Specs

| Adapter | File | Status | Notes |
|---------|------|--------|-------|
| Claude | `claude.md` | Spec | Claude Code verb-to-tool mapping (WebFetch, Read, Edit, Task, etc.) |
| Hermes | `hermes.md` | Spec | Hermes agent framework verb-to-tool mapping |
| Codex / Gemini / others | _not yet written_ | — | Any agent that can read `AGENTS.md` and these specs can bind the verb contract to its own tools |

## How Adapters Are Used

These files are **specifications, not importable modules**. The directory name `runtime-adapters/` contains a hyphen, which is not a valid Python identifier — the `.md` specs document each runtime's verb-to-tool binding so a new runtime can be ported by following the pattern.

A runtime integrates with Spotlight by:

1. Reading `AGENTS.md` (the verb contract + agent manifests)
2. Reading `skills/{skill_id}/SKILL.md` when the orchestrator emits `invoke-skill`
3. Mapping each of the 13 verbs to native tool calls in that runtime (use `claude.md` or `hermes.md` as reference)
4. Enforcing `sensitive: true` by stripping `fetch` and `search` from `allowed_verbs`

For interactive use (Claude Code, Codex, Gemini sessions), the runtime's own AI reads the contract and performs the mapping on the fly — no pre-built adapter code is required.

## Sensitive Mode

When `sensitive: true` is set in AGENTS.md, adapters MUST strip `fetch` and `search` from all agent `allowed_verbs`. Implementations should raise `VerbForbidden` when forbidden verbs are called.
