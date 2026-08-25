# Integrations — External Tools

Spotlight is runtime-agnostic on purpose — but real investigations need more than what the core skills cover. External tools plug into Spotlight as **integrations**: each one is a drop-in directory with a manifest + agent-facing usage doc, discoverable via preflight, routed from the `integrations` skill.

This doc is the operator overview. See `integrations/README.md` for the manifest contract and add-a-new-one procedure. See `skills/integrations/SKILL.md` for the agent-facing routing table.

## What an integration is (vs a skill)

| Concept | Lives at | Example | What it holds |
|---|---|---|---|
| **Skill** | `skills/<id>/SKILL.md` | `osint`, `follow-the-money`, `investigate` | Methodology playbook the agent follows. No credentials. |
| **Integration** | `integrations/<id>/` | `dev-browser`, `junkipedia`, `noosphere-c2pa`, `osint-navigator` | Specific external tool with its own API contract + credentials. |

An agent invokes a **skill** to get *guidance* and calls an **integration** to get *direct data from a specific vendor or platform*. Passive feed signals now live in Mycroft, not Spotlight.

## Current integrations (shipped)

| ID | Type | Category | Key needed | Purpose |
|---|---|---|---|---|
| `arbiter` | api | social-osint | Member-owned `ARBITER_API_KEY` | Browse and create social-media case studies through the native Arbiter HTTPS API, including archived posts, themes, stance, actors, communities, engagement, and report visuals. |
| `dev-browser` | cli | browser-automation | No | Browser automation for specific investigative tasks after ordinary search/scrape is insufficient: forms, portals, JS-rendered pages, screenshots, downloads, and authenticated sessions. |
| `browser-harness` | cli | browser-automation | No | Legacy browser fallback. Do not use as default while dev-browser is green. |
| `browser-use` | library | browser-automation | No (optional cloud) | Legacy/adjacent browser automation. Do not use as default while dev-browser is green. |
| `junkipedia` | api | social-osint | `JUNKIPEDIA_API_KEY` | Narrative / misinformation tracking across social platforms. Application-based access. |
| `maigret` | cli | social-osint | No | Username-led account discovery. Produces unverified profile leads only, not attribution proof. |
| `noosphere-c2pa` | api | provenance-signing | `NOOSPHERE_C2PA_URL` | Optional case-level C2PA/content-credentials signing after Gate 1. |
| `osint-navigator` | api | tool-discovery | `OSINT_NAV_API_KEY` | 10,000+ OSINT tools with AI-powered synthesized answers. Complements the curated 150-tool catalog in the `osint` skill. |
| `unpaywall` | api | academic-open-access | `UNPAYWALL_EMAIL` | Legal open-access lookup for academic papers by DOI. Used only when selected in setup and green in preflight. |

Arbiter is a partner integration. Each user registers with the
[Indicator partner signup link](https://arbiter.simppl.org/auth/register?eventSignup=5cce20c609334e538f07127322361862e3136e3d-324a-4c60-894a-5f42d2d57f8a),
creates their own API key, and configures it locally as `ARBITER_API_KEY`.
Spotlight has no shared key and never receives or supplies one. `ARBITER_API_BASE` is an
optional deployment-matched override. `docs/arbiter-api.md` is the
authoritative interface reference for endpoints, authentication, credits,
pagination, and the case-study lifecycle; Spotlight reaches that interface
directly over HTTPS.

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

`--smoke-test` probes an integration's configured service endpoint when its
manifest declares `smoke_url_env`; otherwise it falls back to the manifest's
public homepage or documentation URL. A configured endpoint returning 401, 403,
or 404 is unusable and fails the probe; 405 may confirm that an endpoint exists
but intentionally rejects `HEAD`. A wholly unconfigured optional set exits
successfully because no requested integration has failed.

Status semantics (same as feed sources):

| Status | Meaning |
|---|---|
| `green` | `env_vars` set (or no keys required) |
| `yellow` | Keys set but a local binary/import probe or optional smoke test failed |
| `red` | One or more required env vars missing |
| `unconfigured` | Optional integration has not been activated; core/local fallback remains available |

The orchestrator runs this at Phase 0 step 10 alongside its Mycroft/passive-monitor checks, giving the user a combined status table at session start.

Example:

```
ID                   Type       Status   Missing env
------------------------------------------------------------------------------
dev-browser          cli        green    —
junkipedia           api        red      JUNKIPEDIA_API_KEY
osint-navigator      api        green    —

green=2  yellow=0  red=1  unconfigured=0
```

## The setup flow (for journalists)

The intended path from website → working install:

1. **Join** at [buriedsignals.com/join](https://buriedsignals.com/join) and install **Indicator Labs**.
2. Pair in the desktop app (Navigator OAuth only). Configure Spotlight from the Engine descriptor. Secrets use the operating-system prompt.
3. Apply the plan. Doctor. Open the chosen agent; skills are already placed.
4. Contributors who already have a checkout can still run `install-spotlight.sh` from GitHub. That is not the journalist path.

Keys are never collected on buriedsignals.com or Pages HTML.

## Adding a new integration

4-step drop-in. See `integrations/README.md` for the full contract.

1. `mkdir integrations/<new_id>`
2. Write `manifest.json` with `id`, `name`, `category`, `type`, `requires_key`, `env_vars`, `capabilities`, `invocable_by`
3. Write `integration.md` with: when to use, exact verb calls, output format, evidence handling, sensitive-mode behavior
4. Add a row to the routing table in `skills/integrations/SKILL.md`

Optionally expose the integration in the local configurator by adding a checkbox + key input to `install/configure.html` (wired through `install/setup_server.py`). Preflight picks up the new integration automatically on its next run — no code changes needed.

## Agent-facing usage

When an agent needs to decide whether to use an integration:

1. `invoke-skill("integrations")` — load the routing table
2. Check current preflight: `execute-shell("python3 integrations/preflight.py --json")`
3. Pick an integration if its status is `green` and capabilities match the task
4. `read-file("integrations/<chosen>/integration.md")` for the exact verb calls
5. Execute; save raw response to `{CASE_DIR}/research/<integration-id>/...` or the integration's documented run directory
6. Cite the integration in the source entry with appropriate `access_method` + `access_notes`

Maigret outputs are always `unverified` leads until the underlying source material has been inspected, archived, and verified.

See `skills/integrations/SKILL.md` § "Routing decision tree" for the task → integration mapping.

## Sensitive mode

In sensitive mode (`AGENTS.md` → `sensitive: true`):

- Integrations that require remote API calls (most of them) become unavailable — `fetch`/`search` verbs are stripped and `execute-shell("curl <remote>")` is guarded at the skill layer
- Preflight still runs; integrations requiring remote APIs report normally (useful as a status check)
- Pre-cached integration responses in `{CASE_DIR}/research/` remain readable via `read-file`
- Local-only browser acquisition against cached or local files may still function through dev-browser — see the individual `integration.md` § Sensitive mode

The orchestrator marks sensitive-mode investigations at Gate 1 noting which integrations were unavailable.

## Reference

| File | Contents |
|---|---|
| `integrations/README.md` | Manifest contract, add-a-new-one procedure, full field reference |
| `integrations/preflight.py` | Env-var + smoke-test checker |
| `integrations/<id>/manifest.json` | Per-integration contract |
| `integrations/<id>/integration.md` | Per-integration agent-facing usage |
| `skills/integrations/SKILL.md` | Agent-facing routing layer |
| `install/configure.html` + `install/setup_server.py` | Local configurator the contributor installer serves on `127.0.0.1` |
