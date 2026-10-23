# Changelog

All notable changes to Spotlight. Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Changed — Decision-model requests mask protected sources

- With Engine installed, `scripts/decision-signals.py` sends every text in a
  decision request through `bsig wiki redact` over the member's wiki
  (`knowledge_destination.workspace_path`) first: protected-source names,
  aliases and identifiers reach the provider as `[protected]`. Engine's
  binary is found through the `spotlight` link Engine installs in
  `~/.local/bin` (`integrations/_engine.py`). A redaction that fails, or a
  wiki named without a findable `bsig`, sends nothing. Without Engine there
  is no protected-source register and the text is unchanged.

### Added — Entity identifiers for wiki links

- The analyst's proposal may give a mention its entity type and strong
  identifiers (company number with jurisdiction, LEI, OpenCorporates,
  Wikidata, email, phone, handle). The builder keeps them on this computer in
  `data/knowledge-mentions.json` and out of the batch, so core batches still
  validate against the full schema; the wiki writer sends them with the
  register lookup, so identified mentions link to `wiki/entities/`. The
  ingest approval binds the file. Other cases' notes keep the links their own
  writes found (`spotlight/entity-links.json`). Two mentions with the same
  text but a different type or unrelated identifiers send the name alone.

### Added — Old cases and the 1.0 graph's retirement

- README and AGENTS describe the forward-only path for old cases and what
  cannot be migrated mechanically.
- `scripts/knowledge_destination.py count-rows` counts the 1.0 graph's
  approved rows, read-only, for Engine's one-time retirement check: an empty
  graph can be removed, and one holding knowledge stays as read-only legacy.

### Changed — Ingest decision checks read Batch 1.1

- The proposition check passes each claim's source positions in its state,
  and its question treats attribution held in a position as no change, so a
  correctly normalized attributed claim is not flagged as broader or
  changed. Memberships resolve through `claim_ref`/`event_ref` with a
  generated event label.
- The question text changed, so `QUESTION_BANK_VERSION` changed. The ingest
  thresholds have not been re-measured: the September labelled set is not in
  this checkout. Until they are, keep ingest decision checks advisory.

### Added — Solo wiki destination

- `scripts/wiki-destination.py` writes an approved Batch 1.1 into the
  member's wiki under `spotlight/`: the batch kept whole, a decision journal,
  and one note per claim, event and story-arc lineage with its status derived
  from the journal. It checks the batch against the digest the request bound,
  admits it in `data/knowledge-ids.json`, records the completed receipt, and
  refuses to overwrite a generated note edited by hand. Entity mentions link
  to the shared register only on an exact match.

### Changed — Solo Ingest reviews the Batch 1.1 on the ingest page

- For an activated case in solo mode (`knowledge_destination.mode`, default
  `solo`), the ingest page shows the batch the builder made, with one
  selection per proposed claim, event and arc and one disposition per
  fact-check flag. Each edit rebuilds the batch (`build-knowledge-batch.py
  --review`), and the page shows the recomputed digest.
- Requesting ingestion needs the final batch, on every channel, and the
  decision's digest covers its bytes, so the request binds exactly the batch
  that was shown. A decline needs no batch.
- Newsroom-mode cases build and write nothing on this computer.
- The decision client can use another endpoint that speaks the Decisions API,
  named only in the install config: https or loopback, its own credential,
  and no OpenRouter key or zero-retention body sent anywhere but
  openrouter.ai.

### Added — Batch 1.1 builder

- `scripts/build-knowledge-batch.py` turns the analyst's proposal
  (`data/knowledge-proposal.json`, interface 1) and an activated case into a
  Batch 1.1 (core): evidence, passages, claim–passage links, source positions
  and assessments come from the case files, and the batch passes the
  cross-record rules or nothing is written.
- Ids persist in `data/knowledge-ids.json`, so a rebuild after review edits
  reuses them; an admitted record is resubmitted only when it changed, at its
  next version.
- An attributed claim without a source position is refused; fact-check flags
  wait for a person's disposition (`--draft` lists them for review).

### Changed — The local harness runs on Pi

- The non-frontier harness is now `harness/pi/`: one Pi RPC launcher per chat,
  isolated worker processes with explicit tool lists, and no model-callable gate
  decisions. Indicator Lab starts it through Osaurus. The runtimes guide, README
  and landing page describe it in place of the Flue harness.

### Added — Source positions

- `data/source-positions.json` records who asserts or disputes each finding:
  actor, stance, cited passages and the attribution verbatim. Producers add
  positions through `scripts/write-source-expressions.py`, which publishes
  them with the passages in one locked write and refuses a position whose
  attribution does not appear in its passages or whose passages are inactive
  or linked to another finding.
- The Investigation-gate binding covers the positions file when it exists;
  validate-case refuses positions changed, added or removed after approval.

### Added — Knowledge Batch 1.1 core

- `schemas/knowledge-batch-1.1.schema.json`: the open-source core of Batch
  1.1, with its names and rules and only the open-source records and fields.
  Every core batch also validates against the full Batch 1.1 schema. The 1.0
  schema stays, and existing cases keep reading against it.
- `scripts/validate-knowledge-batch.py` checks the cross-record rules the
  schema cannot (C1–C10, C13) against a batch and the batches already
  admitted, including the §8.4 flag registry.

### Changed — Exact passages are the default for new cases

- Every new case starts activated: before the first Investigation cycle,
  `scripts/case-contract.py activate-new` writes a `born` activation event,
  and findings and fact-check use contract `1.1`. Existing cases are
  forward-only and keep contract `1.0`.
- One writer for passages: producers no longer write
  `data/source-expressions.json` with file tools.
  `scripts/write-source-expressions.py` merges by expression id, computes
  every hash and fingerprint, validates, and publishes atomically under the
  case lock; `--fingerprint-findings` sets finding fingerprints. Fault-
  injection tests cover interrupted and concurrent writes (pilot blocker 4).
- Artifact hashes are bound when the Investigation gate is approved: the
  approval appends a `binding` event to `case-contract.json` before it
  computes its digest. Until then activated artifacts may change; from then
  on they must match the binding.
- `docs/source-expression-pilot-results.json` records the decision, the stop
  thresholds (locator revalidation at least 95%, validation failures at most
  5% of new cases, review time and correction yield against the legacy
  baseline over the first 10 real cases) and the blockers closed by
  decision, measurement and tests. `scripts/revalidate-locators.py` reports
  the locator revalidation rate across a cases root.

### Changed — Five verdicts and three confidence levels (Batch 1.1)

- New fact-checks use contract `1.1` with Batch 1.1's five verdicts:
  `verified`, `unverified`, `disputed`, `false`, `not_checkable`. Confidence
  is `high`, `medium` or `low`; activated findings (`1.1`) can no longer
  record a finding's confidence as `disputed`.
- `partially_verified` and `mischaracterized` are gone from new work. A
  partly supported finding is split, or becomes `unverified` with a
  `partial_support` flag; a finding that misstates its source keeps the
  verdict for what it asserts and gets a flag naming the misstatement. Claims
  carry optional `flags` from Batch 1.1's code registry (§8.4, including the
  eight codes added on 22 October 2026); a person gives each a disposition.
- Old cases keep their recorded verdicts: contract `1.0` files stay readable
  and nothing rewrites them. An activated case may use only Batch 1.1's
  vocabulary (a case activated before contract `1.1` keeps its `1.0` file),
  and `migrate-source-expressions.py` now blocks a case whose legacy
  verdicts or confidences need a person's judgement.
- The report's verdict chart shows the five verdicts, plus a legacy verdict
  only when an old case records it. A finding with any `not_checkable` part
  reads as not established.

### Changed — Machine identifiers follow the phase names

- Phases are named, not numbered: Preflight, Brief, Methodology,
  Investigation, Report, Ingest. Machine identifiers now match:
  `execution` → `investigation`, `gate1_approval` / `gate1_finalization` →
  `investigation_gate_approval` / `investigation_gate_finalization`, approval
  key `gate1` → `investigation_gate`, attempt kind `execution-cycle` →
  `investigation-cycle`, skills `phase-execution` → `phase-investigation` and
  `phase-gate1` → `phase-investigation-gate`, transition `sealGate1` /
  `seal-gate1` → `sealInvestigationGate` / `seal-investigation-gate`, and
  `decision-signals.py --phase gate1` → `--phase investigation-gate`.
- `data/orchestration.json` moves to `spotlight-orchestration/v2`. A v1 file
  is read under the new names and rewritten as v2 at the next transition; an
  activated case stays activated. `decision-signals.json` files and
  `.spotlight-config.json` decision modes saved under `gate1` are read as the
  Investigation gate.
- The dependency status contract is now
  `spotlight-investigation-gate-dependencies/v2`; dependency digests are
  unchanged, so existing approvals stay valid.

### Changed — Gates are named after their phase

- Docs no longer number gates. Each gate takes the name of the phase it
  closes: Brief, Methodology, Investigation (was "Gate 1"), Report and Ingest.
  Phase 3 is now "Investigation": up to five numbered investigation cycles,
  which are not gates, ending at the Investigation gate. The former Phase 4
  is folded into Phase 3, so Report is Phase 4 and Ingest is Phase 5.

### Removed — Data Navigator source planning

- Data Navigator is retired. The methodology contract no longer has
  `navigator.data_sources`, `navigator_data_not_applicable_reason`, the
  `data_source` query mode or the `data_sources` capability, and
  `scripts/validate-methodology-navigator.py` no longer checks them. Older
  cases that still carry these fields pass the methodology gate, which ignores
  them. A strict JSON Schema check of such a case against
  `schemas/methodology.schema.json` now rejects the old `data_source` mode and
  `data_sources` capability values; Spotlight runs no such check.
- The OSINT Navigator manifest, `osint` skill and Phase 2 prompt describe
  tool discovery only. OSINT Navigator tool discovery and native Arbiter
  access are unchanged.

### Added — REA static-analysis integration

- `integrations/rea/` runs five static REA operations on files inside the case
  directory: Electron/JavaScript apps, HAR or mitmproxy captures, .NET
  assemblies (two views) and Android APKs. REA's runtime capture, browser,
  device, decompiler, setup and update commands are not reachable. The reviewed
  release is `rea-agents@6.4.0`; another version is recorded as a warning.
- `VALIDATED_DEPENDENCIES.md` now lists the optional-integration pins Engine
  does not install (`maigret 0.4.4`, `rea-agents 6.4.0`).

### Added — Web-Check integration

- `integrations/webcheck/` runs website and domain checks one at a time on the
  member's own [Web-Check](https://github.com/lissy93/web-check) instance
  (`WEB_CHECK_URL`). Lookup checks (DNS, RDAP/WHOIS, certificate-log
  subdomains, Wayback history) run by default. Page checks (headers, TLS,
  trackers, tech stack) contact the live host and need `contact_target: true`.
  Port scans, traceroute and third-party scanners are never called. Results
  are unverified observations, hashed in `run-manifest.json`.

### Added — Output hashes in integration run manifests

- `run-manifest.json` now carries `outputs[]` (path, SHA-256 and size of every
  other file in the run directory) and `tool_version`. Maigret records the
  output of `maigret --version`; a failed version probe is a warning.

### Removed — Arbiter client sensitive switch

- `ArbiterClient` and `ArbiterClient.from_env()` no longer accept
  `sensitive`, and `preflight.smoke_test()` no longer accepts it either.
  Nothing in Spotlight set it, so it never blocked a request. As with the
  other remote integrations, the rule not to call Arbiter in sensitive mode
  lives in the skill instructions.

### Fixed — `spotlight-navigator` crashed on every run

- `scripts/navigator-connect` raised `NameError` while building its parser, so
  every invocation failed, including `--status`. It now imports
  `NAVIGATOR_RUNTIME_IDS`. Reported by @JSF204 (#67); @Anai-Guo sent the same
  fix first in #71.

### Fixed — Recorded evidence in the HTML report

- `report.html` shows each finding's recorded evidence again, before the
  independent fact-check, as `findings-report.md` already did. It had been
  dropped when the lead paragraph moved to the editorial summary.

### Fixed — Firecrawl and other CLIs on Windows

- Firecrawl search printed a `UnicodeEncodeError` traceback when results held
  non-Latin text, and a key-less search could hang indefinitely: on timeout
  Python killed only npm's `.cmd` wrapper and kept waiting on pipes the node
  child still held. `integrations/_cli.py` now runs Firecrawl, preflight CLI
  checks and integration runners with no stdin, UTF-8 output and a
  whole-process-tree kill on timeout; the search and scrape CLIs print UTF-8;
  CLI messages lose terminal colour codes. A Windows CI job covers it.

### Added — Keys from a configured credential file

- `integrations/_credentials.py` resolves keys per call without changing the
  process environment. A top-level `env_file` in `.spotlight-config.json` wins
  for every key it holds, and its `# managed: NAME …` line makes it
  authoritative for the listed names even when one is absent. Without
  `env_file`, the environment and the checkout `.env` apply as before.
  Indicator Labs points `env_file` at the file it keeps up to date, so keys
  saved there work in any local agent (Claude Desktop, ChatGPT Desktop, Cursor,
  a terminal CLI) without a launcher.
- `python3 -m integrations.apify` and `python3 -m integrations.junkipedia`
  replace the `curl … $TOKEN` instructions: the key travels in a header, never
  on a command line, and the commands work in PowerShell.
- Firecrawl scrape and search, Arbiter (per request) and decision checks read
  keys through the helper; errors never echo a key.

### Added — Opt-in decision checks (OpenRouter Decisions API)

- New optional integration `decisions` (`integrations/decisions/`): typed
  decision checks through the OpenRouter Decisions API (default model
  `typesafe/jev-1.13`), always with `provider: {zdr: true, allow_fallbacks:
  false}`. It uses the member's `OPENROUTER_API_KEY` (environment,
  `integrations.decisions.env_file`, or the checkout `.env`) and is off unless
  `integrations.decisions.enabled` is true, the case is not sensitive and the
  user opted in for the case. `--check` reports readiness without a request.
- `scripts/decision-signals.py` (the only networked step) writes
  `data/decision-signals.json` (Gate 1 grounding of each finding against its
  located source excerpt, and report-prose fidelity) and
  `data/decision-signals-ingest.json` (proposition vs finding, claim-to-event
  relation, entity type, matching against existing claims), both against
  `schemas/decision-signals.schema.json`. Findings whose quote is not in any
  stored source, or that need arithmetic, are routed to the existing
  fact-check process instead of judged. Writes are locked, atomic and
  symlink-safe; source discovery never leaves the case directory.
- Fingerprints cover every input a result depends on (claim, evidence and
  located source excerpts; prose plus each cited finding's verdict and
  displayed confidence; declared relations and entity types), and stale
  signals are ignored. A choice counts only when both option orders agree.
  Reviewer overrides are bound to the exact fingerprint reviewed and lapse
  when it changes. Malformed answers become `unavailable`; invalid signal
  files fail loudly. Every case input that can reach a request is read
  through descriptors pinned to the case root without following symlinks;
  platforms without that support (Windows) show the integration as
  unavailable.
- Signals can only lower confidence or add flags. In `enforce` mode the
  renderer caps displayed confidence from fresh judged signals and
  `validate-report.py` rejects any confidence above an applied cap; in
  `advisory` mode flags are shown only. `findings-report.md` and
  `evidence-map.json` carry a per-finding decision-check line. Rendering stays
  offline and byte-deterministic; stale signals are ignored.
- The report finalizer gains a `report_fidelity` stage
  (`scripts/check-report-fidelity.py`, offline) that fails in `enforce` mode on
  flagged or changed prose unless a reviewer override is recorded.
- `scripts/ingest-eligibility.py` implements the ingest eligibility gate in code
  (previously applied by the agent from prose) and layers decision signals and
  claim facets (claim type, temporal status, source assertion) on top.
- Evaluation (exploratory; AI-written labels with a blind second labelling):
  on held-out halves in five languages, grounding caught 49/51 problems with
  4/30 false alarms (2/30 after one post-hoc threshold fix); report prose
  22/23 with 0/23; proposition fidelity 18/18 with 1/17.

### Changed — Spotlight native ownership and selected local runtime

- Engine keeps Spotlight bundles and ledger under
  `<Engine base>/skills/spotlight/{bundles,registry.json}` and publishes only
  flat native leaves in the selected checkout/runtime root. Windows and
  Antigravity use verified copies; other local routes use symlinks. Mycroft's
  shared store remains separate. Navigator uses catalog-pinned installed
  distribution bytes; OpenKnowledge remains a knowledge dependency.
- The manifest seals runtime and selected skill ids, including Navigator opt-out.
  Launch requires active committed ownership; migration/recovery uses the plan
  hash, unique apply commit identity, and checked retained receipts. Foreign or
  changed objects are refused and retained, without global fan-out, blind retry,
  manual ledger repair, or whole-ledger rollback.
- Flue runs from the actual checkout with explicit harness config and scoped
  dependency/sandbox environment. Goose keeps its existing workflow; source
  setup is explicitly unmanaged and project-local. External/cloud execution
  without local filesystem access is not supported by this placement.
- Acceptance remains blocked: isolated Goose 1.50.0 discovery of 25 skills,
  Flue SDK/sandbox checks, mixed-ownership lifecycle preservation, and
  browser-repair/Crawl4AI raw-HTML proof do not establish a complete model
  workflow, native Windows, packaged Desktop health, or the original timeout
  cause. This unreleased entry is not a release or full-verification claim.

### Added — Apify as an optional integration

- `integrations/apify/` (manifest + `integration.md`): hosted social-media
  collection actors for X, Instagram, TikTok, Facebook, and LinkedIn through
  the Apify REST API, using the same actor ids as the Mycroft `apify-social`
  recipes. Preflight reports `unconfigured` until `APIFY_API_TOKEN` is set and
  makes no network call; the Engine injects the token when Apify is enabled in
  Indicator Labs, from-repo users export it themselves.
- `social-media-intelligence` reads `APIFY_API_TOKEN` (legacy `APIFY_TOKEN`
  is re-exported, not read directly), makes the `run-sync-get-dataset-items`
  REST path primary with the actor input written to a case-local JSON file,
  and keeps the `apify` CLI as an optional alternative. The X ToS caveat is
  unchanged.
- `phase-preflight` step 2 adds a presence-only Apify check beside the
  Firecrawl one and reports "social-media collection via Apify unavailable —
  export APIFY_API_TOKEN or enable Apify in Indicator Labs" when absent.
- README, `docs/integrations.md`, `integrations/README.md`, `DISCLAIMER.md`,
  and the landing page list Apify with its env var and ToS note.

### Changed — BREAKING: two skill ids renamed

- `investigate` is now `investigation-methodology` and `review` is now
  `editorial-review`. Both old names collide with reserved built-in command
  names in at least one frontier runtime, which silently disables any user
  skill of the same name. Every reference in skills, agents, manifests, docs,
  and tests uses the new ids. The orchestration
  state token `resume_at: review` is unchanged.
- **Existing source installs:** after pulling, refresh the selected checkout's
  project-local leaves so the two new directories are discovered. Inspect any
  legacy global links advertising `review` or `investigate`; retire only entries
  you can prove belong to that unmanaged install. Do not delete foreign content
  or use force-linking to bypass a conflict. Engine-owned migration goes through
  a reviewed Engine plan, not these manual source steps.
- **Engine installs** pin a commit in Engine's signed catalog and are unaffected
  until that pin moves. Re-pinning past this change must update the two
  catalog `id`/`path` entries, regenerate `skills.manifest`, and re-sign.
- **Existing cases** that list `investigate` in `methodology.json`
  `skills_invoked` remain valid: `validate-methodology-navigator.py` maps the
  legacy ids to the new ones. Case artifacts are not rewritten.
- Landing-site URLs `skills/investigate/SKILL.md` and `skills/review/SKILL.md`
  no longer resolve; `llms.txt` and `llms_full.txt` point at the new paths.

### Changed — install preconditions (portable requirements, no runtime branching)

- Phase 0 now requires `CASE_ROOT` to resolve to durable, user-owned storage
  that survives the session, confirmed by a probe write and read-back before any
  case file is created and recorded in `.spotlight-config.json` under `storage`.
  An unconfirmed root stops preflight and asks the user instead of guessing.
- `AGENTS.md` states that `skills/`, `scripts/`, `integrations/`, and `schemas/`
  must be co-located in one checkout reachable from a single shell whose working
  directory is the checkout root, established before Phase 0; Phase 0 verifies
  it. Skills keep their root-relative script paths.
- README source setup uses per-skill native leaves in the selected local
  checkout (linking the repository root discovers nothing), with project
  instructions supplied separately. Some runtimes refresh discovery only at a
  turn/session boundary.

### Removed — plugin marketplace payload

- `plugins/spotlight/` (a generated copy of the repository),
  `.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`,
  `scripts/build-plugin-payload.py`, and `tests/plugin-distribution-check.py`
  are gone. The marketplace route is retired. Engine uses flat project-local
  native leaves, not a generated home-global Claude plugin root.

### Changed — open-source credential guidance

- Install and preflight guidance now distinguishes Indicator Labs' managed
  credential prompts from the public Engine path. Open-source users receive the
  exact Engine credential IDs and use the protected `bsig` stdin/keychain flow;
  no Spotlight localhost page collects secrets.

### Fixed — public installer on macOS and Windows (WSL)

- OpenKnowledge and Spotlight's graph port both require
  `.knowledge-workspace` to be owner-only (`0700`). The installer used
  `mkdir -p`, which follows umask and typically creates `0755` on macOS, then
  `ok init` aborted with `database directory permissions must be owner-only`.
  Setup now creates that directory at `0700` before init, and `init-local`
  chmods a pre-existing directory rather than leaving umask mode in place.
- `ok init` is now non-interactive (`--json --no-skills --scope project`) so
  the installer cannot stall on the editor/skills prompt (`ExitPromptError`).
- The localhost configurator no longer uses `xdg-open`/`gio` on WSL. It opens
  `wslview` or Windows `cmd.exe` instead, and prints the loopback URL if no
  GUI opener works.

### Fixed — Noosphere C2PA signing

- The Noosphere provenance signer now requires an API key (`X-API-Key` with
  `sign` scope). Spotlight sends `NOOSPHERE_PROVENANCE_API_KEY` on signing
  requests and marks the integration contract as key-required.

### Added

- Local reviewed knowledge graph and Open Knowledge projection:
  `knowledge-batch.schema.json` maps exact case findings and source-expression
  fingerprints to canonical, versioned claims, events, story arcs, and
  first-class membership records. SQLite provides exact forward/reverse,
  deduplication, and prior-verdict queries. Spotlight projects a managed
  investigation block plus story pages directly into the journalist's existing
  Open Knowledge workspace, reusing its BGE-M3 index and creating no claim/event
  files or second embedding service. Local receipts, journals, conflict checks,
  deterministic recovery, policy tombstones, and receipt-aware discovery keep
  the same-user path auditable. Engine remains the installer/updater/configurer,
  outside the runtime knowledge path.
- `technical-investigation`, a dynamically loaded CTI capability adapted from
  CTI Expert by Hieu Ngo / chongluadao.vn (GitHub: 7onez): passive indicator and infrastructure history, local
  document/raw-email metadata, public GitHub history, and verified technical-
  indicator export. Compact references keep the 12B manifest-floor increase
  within the 100-token ceiling; reviewed-source locks, daily drift detection, source maps,
  the upstream MIT license, and acknowledgements preserve provenance.
- Arbiter case-study integration (`integrations/arbiter/`): native HTTPS
  `/api/v1` requests use each member's own `ARBITER_API_KEY` and optional
  deployment-matched `ARBITER_API_BASE`. Spotlight owns topic matching,
  reviewed creation, evidence handling, and offline rendering. File-backed
  request/response artifacts preserve raw upstream fields; preflight probes the
  configured deployment's OpenAPI document. Natural-language planning cannot
  approve charged or mutating operations, and errors retain stable Arbiter codes
  and retry metadata.
- Conditional Arbiter analytics section in the deterministic report renderer: a
  case carrying a saved `research/arbiter-report-*.json` gains an engagement
  chart, story, theme table, actors, communities, and recorded source posts,
  rendered by the integration rather than the core template. A case without one
  renders byte-for-byte the same report as before.

### Changed
- Spotlight orchestration now resolves durable state through one importable
  Python authority. Flue registers host-bound active-case
  `spotlight_resolve`/`spotlight_transition` capabilities, while the former CLI
  remains only an optional thin adapter. Execution readiness selects the
  existing Gate 1 owner to author summaries; Report and Ingest remain resolver-
  owned phases. Transition, ingestion, report-decline, and report publication
  writes use descriptor-anchored no-follow replacement on macOS, Linux, and
  Windows through WSL. Legacy review-file processing, direct phase handoffs,
  launcher finalizers, and standalone ingestion routes are removed.
- `/arbiter` is live for members with a configured Arbiter API key. It directs
  each member through the attributed Indicator signup flow and local
  member-owned key setup before browse or create actions. Spotlight does not
  supply or store a shared key; previously saved Arbiter material still
  renders offline.
- `social-media-intelligence` now documents bounded Xquik Tweet and Follower
  Actor routes while preserving existing acquisition and platform-policy
  gates.
- Skill placement now follows the selected project-local native-ownership
  contract described above, superseding the earlier global product namespace
  and product-level runtime adapters.
- `content-access` + `social-media-intelligence` reconciled against upstream @ `2097d218`:
  2026 platform-API facts (Meta Content Library replaces CrowdTangle, X pay-per-use, TikTok
  Research API, Bluesky Jetstream, Threads/Mastodon/Telegram), EU DSA Art. 40 access section,
  external-tooling status block, X collection-authority caveat, OpenAlex step + fixed archive.ph
  command, hard-vs-metered paywall corrections; deliberate-divergence footers added.
- Attribution corrections: "Jay Amditis" -> **Joe Amditis** everywhere (name was wrong);
  `web-archiving` re-attributed to OpenSanctions-inspired methodology (full rewrite, 0%
  verbatim upstream text — diff-verified); `content-access` + `social-media-intelligence`
  keep their Amditis attribution. Added `NOTICE.md`.

> **Next tag will be [2.0.0]** (SemVer major bump): the `grounding` object schema
> drops three fields and `findings.sources` demotes three required fields to
> optional. Both are breaking changes for downstream consumers parsing
> `cases/{project}/data/findings.json`, `fact-check.json`, or
> `provenance-manifest.json`. The classroom profile flag is also removed.

### Added — first-party snapshot tier in web-archiving (Tier 0, default)

- `skills/web-archiving` now captures a first-party **MHTML archive + full-page
  screenshot + response headers** by default, content-addressed by SHA-256, via the
  local `dev-browser` CLI (real Chrome over CDP `Page.captureSnapshot`). Wayback and
  Archive.today remain as third-party complements (Tier 1/2). Borrows OpenSanctions
  Pravda's evidence-layer ideas without its Docker/Postgres/FastAPI stack.
- New `skills/web-archiving/references/snapshot-record.md` (content-addressed record
  schema + `evidence_item` mapping) and `references/capture-dev-browser.md` (capture recipe).
- Tier-0 snapshots are recorded as `evidence-bundle.json` items, and
  `scripts/build-provenance-manifest.py` now hashes the files referenced by
  `raw_path`/`screenshot_path`, so the MHTML + screenshot bytes are tamper-evident inside
  the C2PA-signed manifest.
- Guards: sensitive-mode skips live capture; access-wall detection flags login/paywall
  pages as `human_verification_required` rather than storing them as content evidence.
- Mycroft consumes this skill via the engine cross-over (no separate copy).

### Added — interactive diagrams in report-drafting (default)

- `report.html` now carries interactive mermaid diagrams by default whenever the
  case has relational structure (entity networks, money flows, funnels): zoomable/
  draggable canvas, crisp vector zoom 0.25×–10×, hyperlinked nodes (hover glow + ↗)
  pointing at primary sources, role-color legends, evidence-labeled edges.
- New `skills/report-drafting/references/interactive-diagrams.md` — complete
  copy-paste recipe (mermaid 11 + ELK via CDN) including three non-obvious fixes
  battle-tested in the health-insider investigation: `document.fonts.ready`-deferred
  rendering (label measurement vs webfont race), `foreignObject` overflow (label
  clipping), `useMaxWidth: false` + CSS `zoom` (pixelation at high zoom). Plus the
  per-diagram dagre frontmatter override for cyclic flows, authoring rules
  (≤14 nodes, totals in cluster titles), and a mandatory headless smoke test.
- `report-drafting` SKILL.md: new "Interactive diagrams" section, workflow step 6,
  smoke-test step extended with the headless diagram checks. Node hyperlinks obey
  the existing citation discipline (ground-truth files only).

### Changed — installation architecture (supersedes the browser-generated config blob)

- **One static installer**: `curl -fsSL https://spotlight.buriedsignals.com/install-spotlight.sh | bash`.
  The hosted `setup.html` is now a landing page (how it works, keys checklist,
  command + ZIP fallback); its `spotlight-install.zip` contains only a key-free
  bootstrap that fetches and runs the same canonical script. A run with the
  retired `SPOTLIGHT_CONFIG` base64 blob set fails loud with a pointer to the
  new command — it is never decoded.
- **Local configurator**: the installer serves `install/configure.html` from
  `127.0.0.1` (`install/setup_server.py`, stdlib only, per-run token on every
  GET and POST). All choices move there — mode, runtime, local model + hardware
  fit check, required keys, vault app, install + vault paths with a **native
  folder picker** (osascript/zenity/kdialog) and per-OS default chips, and
  plug-ins. API keys are entered on the local page, **live-validated against
  each provider** (401/403 rejects; network failures never block), staged in
  `~/.config/spotlight/` (0600, atomic), and written to `$SPOTLIGHT_DIR/.env`
  by the install body; the staged secret copy is deleted after the final write
  and on any abort. Keys never appear on a website, in a downloadable artifact,
  in the shell command line, or in shell history. The configurator also writes
  `setup-config.env` and the personalized `getting-started.html` guide that
  opens when the install completes.
- **Headless / CI path**: `bash -s -- --headless` with pre-exported env vars
  (loaded from a 0600 env file via `set -a; . keys.env; set +a`) replaces the
  config blob for automation; the existing `:?` guards enforce the required
  set. Re-runs against a completed install offer to reuse the previous
  configuration instead of reopening the configurator.
- Hard validation: configuration cannot be submitted until required fields are
  present (Firecrawl + OSINT Navigator keys; a provider key when the runtime
  is opencode; install + vault paths, with the vault rejected if it equals or
  nests inside the install dir).

### Removed

- The hosted setup form and client-side `SPOTLIGHT_CONFIG` generator
  (base64 env blob carried through the clipboard and shell history), per-user
  keyed installer ZIPs (`spotlight-setup.zip` / `spotlight-setup.command`),
  and the multi-step macOS Gatekeeper "Open Anyway" walkthrough (the ZIP's
  key-free bootstrap needs only a one-line right-click → Open note).

### Added

- **Vault claims layer.** A fifth note type: standalone, cross-case-queryable
  claim records under `{vault}/claims/` with verdicts, source refs, and
  temporal layering (`layer: durable | lead`, append-only supersession
  history). Strict eligibility gate — only `verified` / `partially_verified`
  findings with grounding above `low` and sources present enter; every
  exclusion is logged. New ingest Step 6 plus claims registry
  (`claims/_registry.json`), generated alias reverse index
  (`entities/_aliases.json`), and human-gated merge proposals
  (`entities/_merge-proposals.json`). Retrieval recipes added to the
  investigator (claim dedup before research, alias resolution before
  semantic search) and fact-checker (prior-verdict citation). Validated by
  `tests/vault-claims-check.py` (wired into `smoke.sh`); existing vaults
  backfill idempotently via `scripts/backfill-claims.py`. Fully additive —
  older vaults without the layer keep working.

- **Gemma 4 E4B Journalist as the default low-RAM local model.** New
  `SPOTLIGHT_LOCAL_MODEL=gemma-e4b` option in `install-spotlight.sh`, paired
  with a third radio card in `setup.html`. Pulls
  `hf.co/tomvaillant/gemma4-e4b-abliterated-journalist-GGUF:Q4_K_M` (~5 GB on
  disk, ~7 GB at runtime, 8B dense, abliterated, journalist fine-tune,
  gemma4 architecture with tool calls and thinking mode). Default selection
  in the setup form; the fit-check now recommends it for any Mac in the
  16–24 GB tier.

### Removed

- **Dormant Mycroft handoff scaffolding.** `install-spotlight.sh` no longer
  creates `handoff-to-mycroft/` in the vault; nothing in Spotlight ever read
  or wrote it. External consumers invoke the spotlight skill or read the
  vault directly — Spotlight carries no consumer-specific scaffolding.

### Fixed

- **Misleading model-size labels.** `install-spotlight.sh` reported
  `OLLAMA_SIZE_LABEL="~8 GB"` for the 26B A4B MoE, conflating active-param
  footprint with the actual 17 GB GGUF blob. Corrected to `~17 GB`. Same
  fix applied to `setup.html` (model-info stat block + the download-size
  blurbs under both llama.cpp and Ollama). The 16 GB recommendation tier
  in the fit-check now points to the E4B Journalist instead of the 26B MoE
  (which OOMs at that RAM level).

### Removed

- **Classroom profile flag.** `SPOTLIGHT_PROFILE=classroom` is no longer
  recognised. The main investigation flow (brief → methodology → multi-cycle
  investigator → fact-checker → Gate 1 → HTML review → ingestion) is the only
  path; runtime is independent of teaching vs newsroom context. Removed
  `docs/classroom-profile.md`, `tests/classroom-profile-check.py`, the
  classroom test in `tests/eval.sh`, and 16 conditional branches in
  `skills/spotlight/SKILL.md`. Reason: profile-as-flag added cognitive load
  for a single concrete use case; if classroom needs return, document them
  in a separate teaching profile rather than as a runtime branch.

### Changed (breaking)

- **`grounding` object trimmed from 10 fields to 7.** Removed
  `grounding_strength` (overlapped `support_type`: `direct` → `full`,
  `insufficient` → `none`), `quote_match` (implied by `support_type` +
  `source_role`), and `contradictions` array (merged into the existing
  free-text `grounding_rationale`). Same trim applied to
  `grounding_assessment` in `fact-check.schema.json` (dropped
  `grounding_strength` and `contradiction_search` — the latter merged into
  `assessment`). Updated:
  `schemas/findings.schema.json`, `schemas/fact-check.schema.json`,
  `skills/epistemic-grounding/SKILL.md`, `skills/review/SKILL.md`,
  `skills/review/references/template.html`,
  `skills/spotlight/references/evidence-grounding.md`,
  `agents/investigator.md`, `agents/fact-checker.md`,
  `scripts/build-provenance-manifest.py`, and all fixtures + tests.
  Reason: the dropped fields were redundant by construction; the cross-check
  theory ("two angles catch errors") did not earn its keep in practice and
  was creating write-fatigue and lower-quality fills.
- **`findings.sources` required fields reduced.** `archive_url`,
  `access_method`, and `local_file` demoted from required to optional in
  `schemas/findings.schema.json`. Same demotion for `archive_url` and
  `access_method` in `fact-check.schema.json`'s `evidence_item`. Reason: in
  practice these are best-effort fields that the validator does not enforce
  presence of. Schema contract now matches actual behaviour.
- **`findings.lead` demoted from required to optional.** Top-level `lead` is
  no longer required in `findings.schema.json`. It remains available as an
  optional field. Reason: never consumed by any renderer or validator;
  required-ness was aspirational.

### Changed (non-breaking)

- **`provenance-manifest.schema.json` cleanup.** `signing.credential_id` and
  `signing.endpoint` removed from `required` array. Their type stays
  `["string", "null"]` so the legitimate "unsigned" state (where the
  signing block exists but credentials/endpoint are null) still validates.
  `claim.grounding_strength` replaced with `claim.support_type` for
  vocabulary consistency with `findings.grounding.support_type`.
- **`findings.schema.json` aligned with real corpus.** Validated against the
  13 real `findings.json` files in `~/buried_signals/investigations/`. The
  schema was previously strict in ways that didn't match what investigators
  actually produced. Changes:
  - `schema_version` and `grounding` demoted from `required` to optional on
    findings (zero of 254 findings across the corpus had a `grounding`
    object; the requirement was aspirational). New investigations still
    emit `grounding` because the agent prompts require it; the schema no
    longer rejects legacy cases.
  - `findings.sources[].url` and `accessed` demoted from `required` to
    optional (Apify-dataset sources use `id`/`description` instead).
  - `findings.evidence` accepts string OR array of strings (scraped-output
    findings legitimately produce an array of evidence items).
  - `findings.sources[].type` enum extended with `search_result`,
    `apify_dataset`, `file`, `url`, `corporate` (all values present in the
    real corpus).
  - `findings.confidence` enum extended with `disputed`.
  - `findings.perspective` converted from enum to free-form string
    (investigators emit analytical-lens values like `ad_tech_forensics`,
    `network_analysis`, `technical_feasibility` alongside the original
    viewpoint values; constraining was creating validation noise without
    editorial signal).
  - Result: 11/13 corpus findings now validate. Remaining 2 fail on
    genuine data bugs (3 findings with empty `claim` text in
    black-cube-tzur; paz-valais-communes uses `commune_checks` shape
    rather than `findings`). The schema correctly rejects these as
    malformed; they need data migration, not schema accommodation.
- **`fact-check.schema.json` aligned with real corpus.** Similar reality
  alignment for the 10 `fact-check.json` files. Changes:
  - `schema_version` and `claims` demoted from `required` to optional
    (some legacy fact-checks use `verdicts` array instead).
  - On `claims[]` items, `grounding_assessment` and `evidence_for` demoted
    from `required` (most corpus cases predate them).
  - `claims[].verdict` enum extended with `partially_verified` and
    `mischaracterized` (used by some cases).
  - `claims[].id` accepts integer OR string (corpus uses both).
  - `summary` accepts object OR string (some cases use essay-style summary).
  - `evidence_item.archive_url` and `local_file` accept null (consistent
    with corpus values).
  - `verdicts` added as optional top-level alternative to `claims` (legacy
    shape).
  - Result: 9/10 corpus fact-checks now validate. Remaining 1 fails on
    7 claims with empty `claim_text` in black-cube-tzur — genuine data bug.
- **Integrations roadmap moved out of skill.**
  `skills/integrations/SKILL.md` no longer carries the "Current deferred
  integrations" bullet list. Moved to `docs/integrations-roadmap.md` with
  an activation checklist. Skill is leaner; roadmap stays tracked.

### Known data bugs (not schema gaps)

- `cases/black-cube-tzur/data/findings.json`: findings F25, F26, F31 have
  no `claim` text.
- `cases/black-cube-tzur/data/fact-check.json`: 7 claims have no
  `claim_text`.
- `cases/paz-valais-communes/data/findings.json`: file uses
  `commune_checks`/`claim_checks` shape instead of `findings`. Needs
  migration or reclassification as a different document type.

### Added

- `scripts/validate-case.py` — write-time validator for a case directory.
  Catches the data bugs surfaced by the v2.0.0 schema reality alignment
  (empty `claim` / `claim_text` fields, missing required top-level keys,
  wrong-shape documents like `commune_checks`/`claim_checks` written into
  `findings.json`, dangling fact-check `finding_id` references). Wired into
  the orchestrator at steps 2.5 and 4.5 of Phase 3 (Execution): after the
  investigator and again after the fact-checker, the orchestrator runs
  `python3 scripts/validate-case.py cases/{project}` and re-spawns the
  agent with the errors quoted if validation fails ("fix the shape only,
  don't change verdicts"). New agent prompt rules in
  `agents/investigator.md` and `agents/fact-checker.md` make the shape
  contract explicit: never emit a finding without claim text; never use
  alternative top-level shapes; skip the finding/claim entirely if it
  can't be articulated. Smoke-tested in `tests/eval.sh` (positive: passes
  sample fixtures; negative: rejects empty `claim`).
- `integrations/browse/` — Browserbase Browse CLI as a second-tier browser
  automation tool, scoped behind a "use when a curated browse.sh skill exists
  for the target portal" trigger. Default `--local` mode (no API key, no cloud
  routing, sovereign by default). Browser Harness remains the primary browser
  fallback for general portal work; Browse fills the gap when someone has
  already mapped a site (OpenCorporates filings, Wayback snapshot search, etc).
  The browse.sh catalog (~100 skills) auto-discovers; the routing table in
  `skills/integrations/SKILL.md` and the decision tree update agents on when
  to prefer Browse over Browser Harness. Browse is not currently part of
  Spotlight's reviewed dependency pins, so it requires manual review before
  installation.
  Detailed routing logic, cost model, sensitive-mode behaviour, and pitfalls
  (per-command CLI latency, accessibility-tree verbosity, fresh-browser default)
  documented in `integrations/browse/integration.md`.

### Changed (earlier in [Unreleased])

- Monitoring ownership split is now explicit:
  - `mycroft` owns passive feed polling, scoring, deduplication, and topic-linked signal storage
  - `spotlight` owns investigation-scoped monitoring orchestration and case linkage
  - `scoutpost` remains unchanged and is used through existing `projects`, `scouts`, and `units` surfaces
- Spotlight no longer ships the legacy `monitoring/feeds/` framework.
- `cases/{project}/data/monitoring.json` is now treated as an external-monitor registry rather than a feed-execution config.

### Added

- `spotlight/monitoring/registry.py` for initializing, normalizing, and migrating `monitoring.json` to schema v2.
- `integrations/scoutpost/` as the default durable-monitor integration surface.
- `mycroft/monitoring/` as the passive-monitor single source of truth, including `poll`, `query`, `preflight`, `topic`, and `prune`.
- Installer-generated `spotlight-doctor` and `spotlight-update` wrappers. Manual updates now fetch `origin/main`, fast-forward only, and run doctor.
- QMD setup in the installer, including automatic registration of the selected vault as the `spotlight` collection.

### Fixed

- Agent setup ZIP now includes local form-provided API key values in the private manifest and instructs local agents to write `.env` without printing secrets.
- Local runtime launch now routes through `spotlight-local` instead of the old `pi` fallback.
- Installer shell startup block is replaced on rerun, so changed runtime, install path, or vault path choices are reflected.

## [1.0.0] — 2026-04-17

Initial public release. Runtime-agnostic OSINT investigation system for journalists.

### Included

- **11 skills**: orchestrator (`spotlight`), `review`, `integrations`, `ingest`, `monitoring`, `web-archiving`, `content-access`, `osint`, `investigate`, `follow-the-money`, `social-media-intelligence`
- **2 agents**: `investigator` (PLANNING + EXECUTION modes), `fact-checker` (independent SIFT methodology)
- **5 JSON schemas**: findings, fact-check, methodology, investigation-log, summary — all `schema_version: "1.0"`
- **5 feed sources**: GDELT, RSS investigative (Bellingcat, ICIJ, Intercept, Crisis Group), RSS regional (17 outlets), GDACS, ACLED
- **3 external integrations**: browser-use, Junkipedia, OSINT Navigator
- **Runtime support**: pi (default for Local mode), Claude Code, Gemini, Codex, OpenCode (OpenRouter / Fireworks / Together)
- **Six readiness criteria** enforced before Gate 1
- **Post-Gate-1 review loop** via self-contained `review.html` artifact
- **Sensitive mode** strips network verbs for local-only investigations
- **One-click install** via `setup.html` — copy-paste or download-and-run
- **`spotlight` shell command** with `update`, `doctor`, `help` subcommands

### Documentation

- 8 user-facing docs under `docs/` (README, structure, runtimes, integrations, investigating, fact-checking, monitoring, recovery)
- `CONTRIBUTING.md` with drop-in patterns for new runtimes, integrations, feed sources, skills
- `DISCLAIMER.md` — editorial responsibility, OPSEC scope, third-party ToS, data handling
- `LICENSE` — MIT, with methodology attributions to Bellingcat, GIJN, Jim Shultz, Joe Amditis, OCCRP/ICIJ

### Tests

- `tests/smoke.sh` — structural integrity (27+ checks, <2s)
- `tests/eval.sh` — contract compliance + sample data validation
- CI via GitHub Actions on every push + PR

### Attribution

Methodology synthesized from:
- Bellingcat training materials (investigate skill)
- GIJN publications (follow-the-money skill)
- Jim Shultz, *Follow the Money* (Revenue Watch / Open Society Institute, 2005)
- Joe Amditis, [claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism) — MIT (web-archiving, content-access, social-media-intelligence)
- Derek Bowler, EBU Eurovision News Spotlight (follow-the-money)
- OCCRP / ICIJ investigative practice

### Known limitations

- Fine-tuned journalism model not yet published on Hugging Face — Local mode installer warns gracefully
- Windows native not supported — WSL required
- OSINT Navigator requires application approval for API access
- Junkipedia access is application-based

### Deferred for future releases

- Integrations: Serus AI, Thinkpol, Reality Defender, Klarety, Scoutpost (awaiting API access or access approval)
- Agent eval harness with real LLM-backed test scenarios
- Release automation + signed installers
- i18n for review.html

---

_Format notes: entries include breaking changes (`!:`), new features (`feat:`), fixes (`fix:`), docs (`docs:`), refactors (`refactor:`), chores (`chore:`). See `CONTRIBUTING.md` for commit-message conventions._
