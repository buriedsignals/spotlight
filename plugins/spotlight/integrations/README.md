# Integrations

External OSINT tools accessible to Spotlight agents during an investigation. Each integration is a drop-in directory with a `manifest.json` (contract) and `integration.md` (agent-facing usage instructions).

This framework is the single place Spotlight models external tools. Integrations use the same discovery pattern throughout the repo — scan `manifest.json`, run preflight, then route through `skills/integrations/SKILL.md`.

## Why integrations are separate from skills

- **Skills** are methodology playbooks: how to investigate a person, how to follow the money, how to verify a claim. Runtime-agnostic, no credentials.
- **Integrations** are specific external tools with credentials and an API contract: Junkipedia's narrative database, Noosphere C2PA's provenance signer, OSINT Navigator's tool index, Unpaywall's DOI lookup, and dev-browser for browser acquisition fallback.

An agent invokes a skill to get *guidance*; it calls an integration to get *data*.

## Current integrations

| ID | Category | Requires key | Env vars |
|---|---|---|---|
| `dev-browser` | browser-automation | No | none |
| `browser-harness` | browser-automation | No | none |
| `browser-use` | browser-automation | No (OSS); optional cloud | `BROWSER_USE_API_KEY` (optional) |
| `junkipedia` | social-osint | Yes | `JUNKIPEDIA_API_KEY` |
| `noosphere-c2pa` | provenance-signing | No | `NOOSPHERE_C2PA_URL`, `NOOSPHERE_C2PA_CREDENTIAL_ID` (optional) |
| `osint-navigator` | tool-discovery | Yes | `OSINT_NAV_API_KEY` |
| `unpaywall` | academic-open-access | Yes | `UNPAYWALL_EMAIL` |

See `skills/integrations/SKILL.md` for the routing table agents use to pick the right integration per investigation task.

## Manifest contract

Every integration directory has a `manifest.json`:

```json
{
  "id": "<integration-id>",
  "name": "Human-readable name",
  "description": "What this integration does",
  "category": "tool-discovery|social-osint|browser-automation|due-diligence|verification|monitoring|…",
  "type": "api|cli|mcp|library",
  "requires_key": true|false,
  "env_vars": ["ENV_VAR_1", "ENV_VAR_2"],
  "activation_env_vars": ["OPTIONAL_INTEGRATION_URL"],
  "smoke_url_env": "OPTIONAL_INTEGRATION_URL",
  "capabilities": ["feature-1", "feature-2"],
  "invocable_by": ["investigator", "fact-checker"],
  "homepage": "https://vendor.example.com",
  "docs": "https://vendor.example.com/docs",
  "rate_limit_note": "optional note about rate limits / quotas"
}
```

| Field | Required | Notes |
|---|---|---|
| `id` | Yes | Directory name (kebab-case) |
| `name` | Yes | Display name |
| `description` | Yes | One-line summary |
| `category` | Yes | Free-form category (used for agent routing) |
| `type` | Yes | How the integration is accessed: `api` (HTTP/REST), `cli` (shell), `mcp` (MCP server), `library` (imported SDK) |
| `requires_key` | Yes | `true` if the integration needs at least one env var to function |
| `env_vars` | Yes | Array of env vars the integration reads. Empty array if none |
| `activation_env_vars` | No | Env vars that explicitly activate an otherwise optional integration. Preflight reports `unconfigured` until all are set |
| `smoke_url_env` | No | Env var containing the integration endpoint to probe with `--smoke-test`; takes precedence over `homepage` and `docs`. Endpoint 401/403/404 responses fail; 405 may confirm a HEAD-rejecting endpoint is reachable |
| `capabilities` | Yes | Array of capability tags (used by `invoke-skill("integrations")` for routing) |
| `invocable_by` | Yes | Which agents may use it: `investigator`, `fact-checker`, `orchestrator`, `user` |
| `homepage` | No | Vendor/project homepage |
| `docs` | No | API documentation URL |
| `rate_limit_note` | No | Free-form note about quotas or throttling |

## Preflight

`integrations/preflight.py` scans every `manifest.json` and reports per-integration status:

| Status | Meaning |
|---|---|
| `green` | `env_vars` are set (or none required) |
| `yellow` | `env_vars` set but smoke-test failed (API down, key invalid, etc.). Only reported when `--smoke-test` is passed |
| `red` | One or more required `env_vars` missing |
| `unconfigured` | Optional integration has not been activated; local/core fallback remains available |

Usage:

```bash
python3 integrations/preflight.py                 # JSON output
python3 integrations/preflight.py --text          # Human-readable table
python3 integrations/preflight.py --smoke-test    # Also runs a minimal API call per integration
```

The orchestrator runs this at Phase 0 step 10, alongside its local Mycroft/passive-monitor checks, so the user sees which integrations are live before starting an investigation.

## Agent-facing usage

Each integration has an `integration.md` that the host runtime reads when the agent decides to use it. These docs describe:

- When to reach for this integration (vs alternatives)
- Exact verb calls the agent should emit (usually `execute-shell(curl ...)` or `execute-shell(tool-cli ...)`)
- Output format the agent can parse
- Rate-limit handling
- Examples

The skill `skills/integrations/SKILL.md` is the routing layer — it tells agents which integration to pick for a given need. The `integration.md` files are the per-tool detail.

## Adding a new integration

4 steps, no central registration:

1. **Directory**: `mkdir integrations/<new_id>/`
2. **Manifest**: write `manifest.json` per the contract above
3. **Usage instructions**: write `integration.md` — when to use, exact verb calls, output format, examples
4. **Verify**: `python3 integrations/preflight.py --text` — the new integration should appear in the status table

Also register in `skills/integrations/SKILL.md`'s routing table so agents can discover it. If the integration is expected to appear in the setup flow, add a checkbox + optional env-var input to the local configurator (`install/configure.html`, wired through `install/setup_server.py`).

No changes required to `preflight.py` itself — it discovers integrations by scanning manifests.

## Sensitive mode

In sensitive mode (`AGENTS.md` → `sensitive: true`):

- Integrations that require remote API calls cannot be invoked (`fetch`/`search` verbs are stripped, and most integrations pipe through those)
- Preflight still runs; integrations requiring remote APIs report normally but the skill-level guard prevents their use
- Local-only browser acquisition against cached or local files can still run through dev-browser if the adapter supports it

## Deferred integrations

Integrations in Tom's pitch deck awaiting API access — architecture ready to absorb them when keys arrive:

- Serus AI — AI-powered due diligence (contact-based access)
- Thinkpol (think-pol.com) — grey web intelligence (28.5B+ data points)
- Reality Defender — deepfake / AI-generated content detection
- Klarety — disinformation detection
Adding any of these is a 4-step drop-in once API access is confirmed. No code changes elsewhere.
