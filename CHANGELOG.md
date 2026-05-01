# Changelog

All notable changes to Spotlight. Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Changed

- Monitoring ownership split is now explicit:
  - `mycroft` owns passive feed polling, scoring, deduplication, and topic-linked signal storage
  - `spotlight` owns investigation-scoped monitoring orchestration and case linkage
  - `cojournalist` remains unchanged and is used through existing `projects`, `scouts`, and `units` surfaces
- Spotlight no longer ships the legacy `monitoring/feeds/` framework.
- `cases/{project}/data/monitoring.json` is now treated as an external-monitor registry rather than a feed-execution config.

### Added

- `spotlight/monitoring/registry.py` for initializing, normalizing, and migrating `monitoring.json` to schema v2.
- `integrations/cojournalist/` as the default durable-monitor integration surface.
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
- `LICENSE` — MIT, with methodology attributions to Bellingcat, GIJN, Jim Shultz, Jay Amditis, OCCRP/ICIJ

### Tests

- `tests/smoke.sh` — structural integrity (27+ checks, <2s)
- `tests/eval.sh` — contract compliance + sample data validation
- CI via GitHub Actions on every push + PR

### Attribution

Methodology synthesized from:
- Bellingcat training materials (investigate skill)
- GIJN publications (follow-the-money skill)
- Jim Shultz, *Follow the Money* (Revenue Watch / Open Society Institute, 2005)
- Jay Amditis, [claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism) — MIT (web-archiving, content-access, social-media-intelligence)
- Derek Bowler, EBU Eurovision News Spotlight (follow-the-money)
- OCCRP / ICIJ investigative practice

### Known limitations

- Fine-tuned journalism model not yet published on Hugging Face — Local mode installer warns gracefully
- Windows native not supported — WSL required
- OSINT Navigator requires application approval for API access
- Junkipedia access is application-based

### Deferred for future releases

- Integrations: Serus AI, Thinkpol, Reality Defender, Klarety, coJournalist (awaiting API access or access approval)
- Agent eval harness with real LLM-backed test scenarios
- Release automation + signed installers
- i18n for review.html

---

_Format notes: entries include breaking changes (`!:`), new features (`feat:`), fixes (`fix:`), docs (`docs:`), refactors (`refactor:`), chores (`chore:`). See `CONTRIBUTING.md` for commit-message conventions._
