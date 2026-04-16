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

## Available Adapters

| Adapter | File | Status | Notes |
|---------|------|--------|-------|
| Claude | `claude.py` | Stable | Claude Code-specific tool bindings |
| Hermes | `hermes.py` | Stable | Hermes agent framework bindings |

## Loading an Adapter

Adapters are loaded by the orchestrator at startup:

```python
from runtime_adapters import load_adapter

adapter = load_adapter("claude")  # or "hermes"
adapter.validate()  # Raises AdapterValidationError if incomplete
```

## Sensitive Mode

When `sensitive: true` is set in AGENTS.md, adapters MUST strip `fetch` and `search` from all agent `allowed_verbs`. Implementations should raise `VerbForbidden` when forbidden verbs are called.
