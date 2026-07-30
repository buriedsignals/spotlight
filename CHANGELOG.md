# Changelog

All notable changes to Spotlight. Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added

- `technical-investigation`, a dynamically loaded CTI capability adapted from
  CTI Expert by Hieu Ngo / chongluadao.vn (GitHub: 7onez): passive indicator and infrastructure history, local
  document/raw-email metadata, public GitHub history, and verified technical-
  indicator export. Compact references keep the 12B manifest-floor increase
  within the 100-token ceiling; reviewed-source locks, daily drift detection, source maps,
  the upstream MIT license, and acknowledgements preserve provenance.
- Arbiter case-study integration (`integrations/arbiter/`): Data Navigator's
  hosted partner source now owns the `/api/v1` credential and all 14 endpoint
  operations, while Spotlight owns topic matching, reviewed creation, evidence
  handling, and offline rendering. Arbitrary text travels through Navigator's
  `--input-file` path instead of shell strings; `docs/arbiter-api.md` documents
  the upstream surface and preflight verifies the `navigator` CLI and deployed
  source. Shared-account access is restricted to trusted Navigator
  administrators; natural-language planning cannot approve charged or mutating
  operations, and hosted errors retain stable Arbiter codes and retry metadata.
- Conditional Arbiter analytics section in the deterministic report renderer: a
  case carrying a saved `research/arbiter-report-*.json` gains an engagement
  chart, story, theme table, actors, communities, and recorded source posts,
  rendered by the integration rather than the core template. A case without one
  renders byte-for-byte the same report as before.

### Changed
- New Arbiter network access is temporarily blocked and `/arbiter` now requires
  the deployed Data Navigator source to report `queryable: true` before it
  offers browse or create actions. Previously saved Arbiter material still
  renders offline while Indicator and Arbiter resolve paid-member ownership.
- Skill placement converged on the engine placement contract (`engine
  docs/skill-placement-contract.md`): canonical `~/.agents/skills/spotlight/` store for every
  runtime + ONE product-level adapter symlink for opencode/pi/Claude Code (which now receives
  skills for the first time); legacy per-skill dirs migrate in place; `spotlight update`
  re-places skills after a fast-forward.
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
