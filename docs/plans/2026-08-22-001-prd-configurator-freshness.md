---
title: "Spotlight configurator freshness: align setup asks with Engine reality"
type: prd
status: draft
date: 2026-08-22
origin: audit requested 2026-08-22 after reviewing configure.html screenshots
---

# Spotlight configurator freshness

## Problem frame

`install/configure.html` (served by `install/setup_server.py`, fetched by
`install-spotlight.sh:121`) still presents choices the product no longer has,
and misses capabilities Engine now owns. Two form paths exist:

1. **Static form** — public installs without Engine (`bsig` absent or
   `--legacy-only`). This is what the screenshots show.
2. **Engine-managed form** — `configure.html:799-920` replaces the whole static
   form (`form.replaceChildren()`) with a descriptor-driven field list from
   `GET /engine-descriptor` when Engine is present.

The static path has drifted furthest. The Engine-managed path is current by
construction but renders as an unstyled bare `<select>` list and drops several
static-form affordances.

## Findings

Each finding cites the current source of truth it contradicts.

### F1 — Vault app selection (Obsidian / Tolaria) is retired

- Page: `configure.html:287-306` asks users to pick a vault **app** ("Pick the
  app you read it with"), requires Obsidian CLI toggle, validates Tolaria
  presence at `setup_server.py:301-306`.
- Installer body: `install-spotlight.sh:235` defaults `SPOTLIGHT_VAULT_APP=obsidian`;
  `:845-886` brew-installs Obsidian and coaches the CLI toggle; `:1533-1534`
  opens Obsidian at the end; `:1394-1395` persists `vault_type`/`vault_app`.
- Current truth: OpenKnowledge is the sole knowledge runtime.
  - `engine/internal/configure/resolver.go:157` — `knowledge_backend` offers
    exactly one option, `openknowledge`.
  - `spotlight` commit `b19760c` "make OpenKnowledge the sole Spotlight runtime";
    `install-spotlight.sh:1070` hard-requires the `open-knowledge` CLI;
### F2 — Runtime auto-detection exists in Engine v0.1.12, but Spotlight setup does not consume it

- Shipped: `detectSupportedRemoteMcpRuntimes`
  (`engine/desktop/src/main/remote-mcp-runtime.ts:33`) probes installed
  `claude-code` / `codex-cli` binaries, captures versions, and the handoff UI
  auto-selects the first detected runtime
  (`renderer-remote-mcp.tsx` — `setRuntime(next.installed[0]?.id ?? …)`),
  keeping manual choices as fallback. Landed with the Navigator/Scoutpost MCP
  handoff work (`95b476f`, plan `2026-08-20-002-followup-native-mcp-installation.md`)
  and shipped in desktop v0.1.12 (`14cf5d6`, bumped 2026-08-22).
- Scope gap: this probe is consumed only by the Engine desktop app's
  Navigator/Scoutpost remote-MCP handoff cards (`renderer.tsx:313-314`). The
  Spotlight configurator is a separate artifact — `configure.html` served by
  `install/setup_server.py` from the spotlight repo — and never calls the
  probe. Its runtime section remains a full manual choice list
  (`claude | pi | codex | opencode`, `configure.html:176-201`), and so does
  the Engine descriptor path (`resolver.go:68-79`,
  `SpotlightSetupView.swift:293`).

**Decision (approved by Tom, 2026-08-22; trimmed by simplicity review the
same day):** wire detection into **Spotlight setup only**. Splash was cut by
review: `splash/installer/configure.mjs` has no runtime-selection surface to
auto-select, and its `place-skills.mjs#detectHosts` report answers a
different question (skill-door placement, documented as "evidence and not
proof"). Wiring there would build machinery with no user-facing question.
Requirements:

- **Minimal mechanism, named.** Port the probe's semantics (~20 lines:
  `which <bin>` + `--version` capture) into `setup_server.py` behind an
  injectable seam, stubbed in tests exactly as
  `renderer-remote-mcp.test.tsx` stubs the original. The Engine TS probe is
  the reference implementation, not a shared dependency — no cross-language
  shared service, IPC, or Node-from-Python machinery. No bsig subcommand for
  detection exists; adding one is out of scope.
- **Auto-select when detected; manual selection stays.** When a supported
  runtime is found, preselect it and collapse §02 to a confirmation row
  ("Detected Claude Code <version> — using it", change link). When nothing is
  detected, render the full choice list exactly as today. The shipped handoff
  UI already models this fallback (`installed[0]?.id ??
  manualChoices[0] ?? 'generic-manual'`, `renderer-remote-mcp.tsx:63`); the
  configurator adopts the same shape.
- **Honest badges instead of new probes.** `defaultRuntimeProbe` answers
  only `claude-code` / `codex-cli` (`remote-mcp-runtime.ts:20`); everything
  else returns `installed:false`. Options without probe coverage get an
  explicit "not auto-detected" treatment and always render in the manual
  list — detection must never silently narrow the offer. New probes for
  `pi` / `opencode` / Desktop surfaces are deferred until a consumer needs
  them (see Follow-ups).
- **Copy discipline:** binary presence proves installation, not subscription
  entitlement — say "detected Claude Code installed", never "we detected your
  subscription".
- **Tokens stay descriptor-sourced** (closes F3 alongside): whatever the
  detector finds is mapped once, in `setup_server.py`, to resolver tokens
  before submit.

### F3 — Runtime vocabulary drift between the two form paths

Static page tokens (`claude`, `codex`) differ from resolver tokens
(`claude-code`, `codex-cli`, …) and omit both Desktop surfaces entirely. Any
future hand-off of page tokens to `bsig configure validate` fails
(`normalize.go:31-35` rejects unknown values; choice validation admits only
descriptor IDs).

**Required change:** single token set = descriptor tokens. If the legacy path
stays, map tokens in one place (`setup_server.py`) and add a consistency test
like the retired `tests/eval.sh` runtime greps.

### F4 — Firecrawl: page says optional, Engine contract says required

- Page/server: "optional fallback", warn-only
  (`configure.html:266-272`, `setup_server.py:311-312`).
- Engine: spotlight descriptor ships `RequiredSecretIDs =
  ["FIRECRAWL_API_KEY"]` unconditionally (`resolver.go:124-126`), so
  `EngineBridge.submit` hard-fails without it
  (`engine_bridge.py:47-68`, raise at `:68`: "Missing required
  credential(s)").

One of the two is wrong. If sovereign SearXNG/Crawl4AI genuinely suffice,
drop `FIRECRAWL_API_KEY` from `RequiredSecretIDs`; if not, mark the field
required on the page. Do not ship both claims.

### F5 — Local model cards contradict the signed catalog

| Claim on page | Signed catalog (`catalog/catalog.json`) |
|---|---|
| Gemma 4 12B badge "16 GB", "runs on any 16 GB machine" (`configure.html:239-241`) | `spotlight-gemma4-12b` `min_ram_gb: 24` |
| Default local model = gemma12b (`configure.html:238`) | `recommendation: "default"` is `gemma4-26b-a4b` (32 GB); 12b is `advanced` |
| Fit-check auto-picks its own tiers (`configure.html:498+`) | Engine owns model recommendation (`resolver.go:94`, `recommendedOption(localModels)`) |

**Required change:** render local-model cards from the catalog (id, min RAM,
download size, recommendation) instead of pinned prose; keep the fit-check as
a filter, not a second source of truth.

### F6 — Workspace path default mismatch — RESOLVED (gate B, 2026-08-22)

Page default vault path `~/Intelligence` (`configure.html:318`); Engine
descriptor `workspace_path` default `~/Documents/OpenKnowledge`
(`resolver.go:100`); Engine desktop uses `~/Intelligence`
(`SpotlightSetupView.swift:38`). Three defaults across three surfaces — and
the resolver default is identical for Spotlight and Mycroft
(`resolver.go:100` and `:108`), so a machine with both products installed
collides on one workspace directory today.

**Decision (approved by Tom, 2026-08-22):** canonical parent is
`~/Documents/OpenKnowledge`; each product owns a **sibling** child workspace
beneath it:

- Spotlight: `~/Documents/OpenKnowledge/Spotlight`
- Mycroft: `~/Documents/OpenKnowledge/Mycroft`

Rules:
- The two workspaces are siblings, never merged and never nested in each
  other — Spotlight and Mycroft are separate products that coexist or are
  used independently; sharing one OpenKnowledge parent is the only coupling.
- Both-installed case: each product's descriptor default resolves to its own
  child path, so coexistence needs no user arbitration. Engine's
  `ApplyDefaults` (`resolver.go:160+`) and the desktop app must carry the
  same per-product defaults.
- The configurator's install/vault separation rule ("vault must not live
  inside the install folder", `setup_server.py:292-300`) still applies; only
  the default changes, the field stays user-editable.
- Migration: existing installs keep their recorded workspace path
  (`.spotlight-config.json` already warns "old vault data is not migrated",
  `install-spotlight.sh:1386`); the new default applies to fresh installs
  and is surfaced in the confirmation UI, not silently renamed.

### F7 — Engine-managed replacement UI discards static-form features

When Engine is detected, `form.replaceChildren()` erases the Navigator email
flow, fit-check, integration checkboxes styling, and the entire design system,
substituting raw selects (`configure.html:809-896`). The descriptor carries a
`navigator` connection (`resolver.go:118-121`) that this renderer never draws.
Whatever else is decided, this path needs either the Spotlight design shell or
a decision that Engine-managed installs go through the desktop app only.

## Proposed scope (gates A and B resolved 2026-08-22)

1. Remove vault-app selection and all Obsidian/Tolaria behavior from page,
   server, installer body, config schema (F1).
2. Wire runtime detection into Spotlight setup: auto-select detected
   runtimes with manual selection preserved as fallback; honest
   "not auto-detected" badges for uncovered runtimes; single token mapping
   (F2/F3).
3. Resolve Firecrawl required-vs-optional at the contract level, then align
   page copy (F4).
4. Catalog-drive the local model cards (F5).
5. Per-product sibling workspace defaults under `~/Documents/OpenKnowledge`
   — `Spotlight` / `Mycroft` children, coexistence-safe (F6).
6. Decide the future of the legacy static form vs Engine-only setup (F7).

Non-goals: getting-started page redesign; Mycroft configurator (already
migrated to OpenKnowledge); Navigator/Scoutpost MCP handoff flows beyond
reusing their probe as reference; **Splash** (cut by simplicity review
2026-08-22 — its configurator has no runtime-selection surface, and
`place-skills.mjs#detectHosts` is a skill-door placement report answering a
different question; revisit only against a Splash-repo drift finding).


## Verification contract

- `tests/install-spotlight-check.sh` extended: no `vault_app`/`tolaria`/
  `obsidian` references remain in served artifacts; dry-run install succeeds
  with no Obsidian steps.
- New consistency test: every runtime/model/provider ID offered by
  `configure.html` exists in the resolved Engine descriptor (or is explicitly
  mapped in `setup_server.py`, asserted by test).
- Detection behavior tests (stubbed probes, mirroring
  `renderer-remote-mcp.test.tsx`):
  - detected runtime → §02 collapsed to a confirmation row, submit payload
    carries the resolver token;
  - no runtime detected → full manual choice list renders and submits;
  - probe error/timeout → degrades to the manual list, never blocks install.

- Manual: run `install-spotlight.sh --dry-run` locally in both modes
  (with and without `bsig` on PATH); screenshots of both form paths, plus one
  screenshot showing the collapsed §02 confirmation row on a machine with
  Claude Code installed.
- Workspace-default test: with both products resolved from one catalog, the
  spotlight descriptor default is `~/Documents/OpenKnowledge/Spotlight` and
  the mycroft default is `~/Documents/OpenKnowledge/Mycroft` — distinct
  siblings; an existing recorded path in the install manifest wins over the
  default.

## Follow-ups (out of scope until a consumer exists)

- Probe coverage for `pi` / `opencode` / Desktop surfaces in the Spotlight
  configurator detection seam.
- Splash-side runtime wiring, if a Splash setup question ever needs it.
