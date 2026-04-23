# Contributing to Spotlight

Spotlight is runtime-agnostic by design. Every subsystem is structured as a drop-in: add one directory or one table entry, and Spotlight picks it up. No central registration, no build step.

This doc is the entry point. It points at the per-subsystem contracts.

## Before you start

- Read `docs/structure.md` to understand the layout.
- Read `AGENTS.md` — the runtime contract every skill and agent speaks.
- Run `bash tests/smoke.sh` — if it passes on your machine, your environment is ready.
- Check `docs/plans/` for any active work-in-progress.

## Ways to contribute

### Add a new runtime (agent harness)

A runtime is an agent CLI or SDK (pi, Claude Code, Gemini, Codex, OpenCode, etc.) that can read `AGENTS.md` and dispatch the 13 verbs.

1. Add an entry to the `RUNTIMES` const in `setup.html` (single source of truth):
   ```js
   myrt: {
     name: 'My Runtime',
     bin: 'myrt',
     installPkg: '@vendor/my-runtime',
     contextFile: 'MYRT.md',  // or null if it reads AGENTS.md natively
     keyLabel: 'My Runtime API key',
     keyPlaceholder: 'mrt-...',
     envVar: 'MYRT_API_KEY',
   }
   ```
2. Add a radio button to `#cloud-group` in `setup.html` (or create a new mode if it's a local runtime).
3. Add a `.step-card` with `id="cloud-usage-myrt"` describing the three-step usage flow (start-with-`spotlight` → type command → agent walks through pipeline).
4. Write `docs/runtimes.md` addition — brief wiring notes, verb mapping specifics, sensitive-mode handling.
5. Run `bash tests/smoke.sh` and `bash tests/eval.sh` to confirm consistency.

### Add a new external tool integration

An integration is a specific external OSINT tool (Junkipedia, browser-use, OSINT Navigator, etc.).

1. `mkdir integrations/<id>/`
2. Write `integrations/<id>/manifest.json` per the contract in `integrations/README.md`.
3. Write `integrations/<id>/integration.md` — when to use, verb calls, output format, sensitive-mode behavior.
4. Add a row to the routing table in `skills/integrations/SKILL.md`.
5. Optional: add a checkbox to the Integrations section of `setup.html` so journalists see it during install.
6. Run `python3 integrations/preflight.py --text` — your new integration should appear.

### Passive feed sources

Passive feed-source code moved to Mycroft. Do not add new monitoring feeds to Spotlight.

If you need a new passive source:

1. Add it to Mycroft's monitoring service.
2. Update Spotlight's monitoring references only if the orchestrator needs to know the source exists.
3. Keep Spotlight focused on recommendation, approval, and cross-tool linkage.

### Add a new skill

A skill is a methodology playbook, not a tool — things like "how to investigate a person" or "how to archive evidence."

1. `mkdir skills/<id>/`
2. Write `skills/<id>/SKILL.md` with YAML frontmatter:
   ```yaml
   ---
   name: <id>
   description: <one-liner>
   version: "1.0"
   invocable_by: [investigator | fact-checker | orchestrator | user]
   requires: [<other-skill-id>]  # optional
   ---
   ```
3. Add a row to the skill registry in `AGENTS.md`.
4. Reference the skill from `skills/spotlight/SKILL.md` or another orchestrator as appropriate.
5. Update `docs/structure.md` skill count.
6. Run `bash tests/smoke.sh` — the skill-count check should now pass at the new total.

## Testing

```bash
bash tests/smoke.sh   # structural + cleanliness checks (fast, <2s)
bash tests/eval.sh    # contract compliance + sample data validation
```

CI runs both on every push + PR. Don't merge unless both are green.

When touching `setup.html`'s `buildScript()`, also run the local config validator:

```bash
node /tmp/setup-test.js  # generates sample scripts for all runtimes, bash -n each
```

(See `.github/workflows/ci.yml` for the canonical test commands.)

## Coding standards

- **No new Claude-specific syntax** in skills or agents. Use the 13 abstract verbs from `AGENTS.md`. The smoke test greps for `WebFetch`, `WebSearch`, `Agent(`, `Skill(`, `allowedTools`, `maxTurns`, etc. and fails if any appear.
- **Every skill has YAML frontmatter** with at least `name`, `description`, `version`, `invocable_by`.
- **Every integration manifest declares `env_vars`** (empty array if none required) and `requires_key`.
- **Generated files (scripts, JSON) must be validated.** `tests/eval.sh` runs `jsonschema` on sample case files; add fixtures for new schemas.
- **No hardcoded personal emails or paths in committed code.** Use env vars (`$CONTACT_EMAIL`, etc.) or configurable placeholders.

## Commit messages

Short, imperative subject (50 chars) + blank line + body explaining the "why" (not the "what"). For breaking changes, lead the subject with `!:`.

Skip the Claude co-author trailer in PR commits — it's fine on main but noisy in PR review.

## PR process

1. Fork + branch from `main`.
2. Keep PRs focused — one concern per PR. Splitting is better than bundling.
3. Fill out `.github/PULL_REQUEST_TEMPLATE.md` (auto-prompted on PR creation).
4. CI must pass (`tests/smoke.sh` + `tests/eval.sh`).
5. For new runtimes / integrations / skills: include a brief `docs/` addition + update the relevant registry.
6. Reviewer merges via squash-and-merge to keep `main` history linear.

## Security

If you find a security issue (credential leak pattern, API key exposure, arbitrary-code-execution in generated scripts, etc.), **email buriedsignals@agentmail.com** before filing a public issue. We'll coordinate disclosure.

Do NOT open a public issue for anything that could enable an attacker.

## Attribution

Non-trivial methodology contributions deserve attribution in the affected skill's frontmatter (`attribution:` field) and in `LICENSE`. We take journalism-methodology provenance seriously.

## Questions

- General: open a GitHub Discussion
- Bugs: use the bug report issue template
- Features: use the feature request template
- Security: buriedsignals@agentmail.com
