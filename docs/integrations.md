# Integrations — External Tools

Spotlight is runtime-agnostic on purpose — but real investigations need more than what the core skills cover. External tools plug into Spotlight as **integrations**: each one is a drop-in directory with a manifest + agent-facing usage doc, discoverable via preflight, routed from the `integrations` skill.

This doc is the operator overview. See `integrations/README.md` for the manifest contract and add-a-new-one procedure. See `skills/integrations/SKILL.md` for the agent-facing routing table.

## What an integration is (vs a skill)

| Concept | Lives at | Example | What it holds |
|---|---|---|---|
| **Skill** | `skills/<id>/SKILL.md` | `osint`, `follow-the-money`, `investigate` | Methodology playbook the agent follows. No credentials. |
| **Integration** | `integrations/<id>/` | `browser-use`, `junkipedia`, `osint-navigator` | Specific external tool with its own API contract + credentials. |

An agent invokes a **skill** to get *guidance* and calls an **integration** to get *direct data from a specific vendor or platform*. Passive feed signals now live in Mycroft, not Spotlight.

## Current integrations (shipped)

| ID | Type | Category | Key needed | Purpose |
|---|---|---|---|---|
| `browser-use` | library | browser-automation | No (optional cloud) | Agent-driven browser automation — form navigation, JS-rendered extraction, multi-step flows. MIT open source. |
| `junkipedia` | api | social-osint | `JUNKIPEDIA_API_KEY` | Narrative / misinformation tracking across social platforms. Application-based access. |
| `osint-navigator` | api | tool-discovery | `OSINT_NAV_API_KEY` | 1,000+ OSINT tools with AI-powered synthesized answers. Complements the curated 150-tool catalog in the `osint` skill. |
| `cojournalist` | api | monitoring | `COJOURNALIST_API_KEY` | Durable monitoring via existing coJournalist projects, scouts, and information units. |

## Deferred integrations (architecture ready)

These are documented in the pitch deck and have interest from journalism orgs, but either require direct vendor access (no public API) or application workflows that haven't completed. The integrations framework accepts them the moment access lands — a 3-file drop-in.

| ID | Status | Category | Notes |
|---|---|---|---|
| `serus` | Awaiting API access | due-diligence / OSINT | serus.ai — surface + dark web + public data aggregation. SaaS with enterprise API on request. |
| `thinkpol` | Awaiting API access | grey-web-intelligence | think-pol.com — 28.5B+ data point private archive. Enterprise access. |
| `reality-defender` | Awaiting API access | verification | Deepfake / AI-generated content detection. Has API, enterprise pricing. |
| `klarety` | Awaiting API access | verification | Disinformation detection. |
`vera.ai` was evaluated but excluded — it's an EU research project shipping browser plugins + platform-integrated tools, not a programmatic API. Listed in `osint` skill references as a journalist resource.

## Preflight

Every integration's readiness is checked by `integrations/preflight.py`:

```bash
python3 integrations/preflight.py                 # JSON output
python3 integrations/preflight.py --text          # Human-readable table
python3 integrations/preflight.py --smoke-test    # Also run a minimal probe per integration
```

Status semantics (same as feed sources):

| Status | Meaning |
|---|---|
| `green` | `env_vars` set (or no keys required) |
| `yellow` | Keys set but smoke-test failed (only reported with `--smoke-test`) |
| `red` | One or more required env vars missing |

The orchestrator runs this at Phase 0 step 10 alongside its Mycroft/passive-monitor checks, giving the user a combined status table at session start.

Example:

```
ID                   Type       Status   Missing env
------------------------------------------------------------------------------
browser-use          library    green    —
junkipedia           api        red      JUNKIPEDIA_API_KEY
osint-navigator      api        green    —

green=2  yellow=0  red=1
```

## The setup flow (for journalists)

The intended path from website → working install:

1. **Landing page** on buriedsignals.com (separate work) links to the hosted setup page
2. **`setup.html`** opens in the journalist's browser — runtime picker, integration checkboxes, API key inputs, vault config
3. Journalist clicks **Generate installer**
4. Two install options:
   - **Option A — Copy into Terminal:** click "Copy script", open Terminal (⌘+Space → Terminal), paste (⌘+V), Enter
   - **Option B — Download installer:** click "Download spotlight-setup.zip", extract, double-click the `.command` file
5. Script installs: firecrawl CLI + chosen runtime + selected integrations + `.env` (chmod 600) + `.spotlight-config.json`; runs preflight for sanity-check
6. Journalist runs the chosen runtime (e.g. `pi`) and starts investigating

The script is identical for both options — only the delivery differs. Keys written into the user's `.env` never leave their machine.

## Adding a new integration

4-step drop-in. See `integrations/README.md` for the full contract.

1. `mkdir integrations/<new_id>`
2. Write `manifest.json` with `id`, `name`, `category`, `type`, `requires_key`, `env_vars`, `capabilities`, `invocable_by`
3. Write `integration.md` with: when to use, exact verb calls, output format, evidence handling, sensitive-mode behavior
4. Add a row to the routing table in `skills/integrations/SKILL.md`

Optionally expose the integration in `setup.html` by adding a checkbox + key input to the form. Preflight picks up the new integration automatically on its next run — no code changes needed.

## Agent-facing usage

When an agent needs to decide whether to use an integration:

1. `invoke-skill("integrations")` — load the routing table
2. Check current preflight: `execute-shell("python3 integrations/preflight.py --json")`
3. Pick an integration if its status is `green` and capabilities match the task
4. `read-file("integrations/<chosen>/integration.md")` for the exact verb calls
5. Execute; save raw response to `cases/{project}/research/<integration-id>-<slug>-<timestamp>.json`
6. Cite the integration in the source entry with appropriate `access_method` + `access_notes`

See `skills/integrations/SKILL.md` § "Routing decision tree" for the task → integration mapping.

## Sensitive mode

In sensitive mode (`AGENTS.md` → `sensitive: true`):

- Integrations that require remote API calls (most of them) become unavailable — `fetch`/`search` verbs are stripped and `execute-shell("curl <remote>")` is guarded at the skill layer
- Preflight still runs; integrations requiring remote APIs report normally (useful as a status check)
- Pre-cached integration responses in `cases/{project}/research/` remain readable via `read-file`
- Local-only library integrations (e.g. browser-use against a local HTML file) may still function — see the individual `integration.md` § Sensitive mode

The orchestrator marks sensitive-mode investigations at Gate 1 noting which integrations were unavailable.

## Reference

| File | Contents |
|---|---|
| `integrations/README.md` | Manifest contract, add-a-new-one procedure, full field reference |
| `integrations/preflight.py` | Env-var + smoke-test checker |
| `integrations/<id>/manifest.json` | Per-integration contract |
| `integrations/<id>/integration.md` | Per-integration agent-facing usage |
| `skills/integrations/SKILL.md` | Agent-facing routing layer |
| `setup.html` | Journalist-facing installer generator |
