# Recovery — When Things Break Mid-Investigation

Agents crash, APIs rate-limit, your laptop closes the lid. Spotlight is designed around file-based state so recovery is cheap. This doc covers the common failure modes.

## The golden rule

**Everything Spotlight knows is in `cases/{project}/data/*.json`.** No hidden daemon, no lock service, no in-memory state. If a cycle crashes, re-read the JSON and keep going.

## Failure scenarios

### Agent crashes mid-cycle

The investigator or fact-checker hit an iteration limit, an LLM error, or a tool timeout. The orchestrator's cycle loop catches this.

Recovery:
1. Check `cases/{project}/data/investigation-log.json` — does the current cycle have an entry? If no, the investigator didn't complete; re-spawn it with the same methodology.
2. Check `cases/{project}/data/findings.json` — is the `cycle` field equal to the current cycle? If no, partial write; the investigator spawn should merge with what's there.
3. Check `cases/{project}/data/fact-check.json` — present and matching cycle? If no, re-spawn the fact-checker.
4. If both JSONs are intact for this cycle, proceed to readiness evaluation.

Re-spawning is safe. The investigator's EXECUTION prompt includes "merge with prior findings, do not duplicate" instructions. The fact-checker re-reads findings.json from scratch each time.

### Laptop sleeps / Terminal closes

No special handling needed. Reopen Terminal, `cd` into the Spotlight repo, run `spotlight` — the orchestrator reads the case directory and determines where to resume per the "Context Recovery" table in `skills/spotlight/SKILL.md`.

| Files present | Resume at |
|---|---|
| None | Phase 1 (Brief) |
| `brief-directions.txt` only | Phase 2 (Methodology) |
| `data/methodology.json` but no findings | Phase 3, cycle 1 |
| `data/findings.json` but no `summary.md` | Phase 3, evaluate current cycle readiness |
| `summary.md` present | Phase 4 (Gate 1 review) |

### Firecrawl / OSINT Navigator API fails

Rate-limited, quota exhausted, or network glitch.

- Firecrawl: errors are retried by the agent up to 3 times with exponential backoff. If still failing, the agent records the attempted URL in `investigation-log.json` under `failed_approaches` and continues with what it has. A finding citing an unreachable URL gets `access_method: inaccessible` and `confidence: low`.
- Navigator: non-critical. If Navigator is unreachable, the agent falls back to the curated 150-tool catalog in `skills/osint/references/tools-by-category.md`. Log shows `navigator_degraded: true` for the cycle.

Re-run later if the gap matters. No data loss.

### Ollama / llama-server crashes (Local mode)

The local model server died. Symptoms: pi hangs, generic HTTP 502 errors.

Fix:
```bash
# Check Ollama is running
ollama list

# Restart if needed
brew services restart ollama

# Then re-run spotlight
spotlight
```

Model state is on disk; restarting doesn't lose anything. Your investigation files are untouched.

### Obsidian vault locked / ingestion mid-failure

The ingest skill uses a `.ingest-lock` file. If a previous ingestion crashed, the lock may be stale.

1. Check: `ls {vault}/.ingest-lock`
2. If present but old (check mtime): delete it → `rm {vault}/.ingest-lock`
3. Re-run ingestion. It will re-read registries, see what's already written, and skip duplicates (matched by `id` in the relevant registry).

The ingest skill is idempotent at the registry level — you can run it twice without double-entries.

### Vault sync conflict (Obsidian Sync or other)

If two devices ingest simultaneously (rare), the `_registry.json` files may conflict. Resolution:

1. Accept the most-recent version as ground truth (usually the one with more entries).
2. Re-run ingestion from the case that was lost — it will fill in any missing notes.

### Review feedback never processes

You submitted `review-feedback.json` but the next `spotlight` run didn't act on it.

Check:
1. Is the file at `cases/{project}/data/review-feedback.json`?
2. Is there a `cases/{project}/data/review-feedback-processed.json` marker? If yes, the file was already processed; only newer feedback (later `submitted_at`) will re-trigger.
3. Is the `schema_version` field `"1.0"`? Malformed feedback files are skipped with a warning.

Delete the processed marker and re-run if you want to force re-processing.

### Corrupted case JSON

If a cycle wrote malformed JSON (e.g. the agent was interrupted mid-write):

1. Check `git log cases/{project}/` if the case is under git (rare — `cases/` is gitignored by default).
2. Restore from a `.bak` if you kept one.
3. Otherwise: the safest recovery is to delete the corrupted file and re-spawn the agent that writes it. Findings / fact-check / investigation-log are all append/overwrite, not append-only.
4. If `findings.json` is lost entirely, start the cycle over — the investigator's research files in `cases/{project}/research/` are still there, so the re-run is fast.

## Nuclear option: start fresh

Backup first: `cp -r cases/{project} cases/{project}-backup-$(date +%s)`

Then: `rm -rf cases/{project}/data/` and re-run `spotlight`. The orchestrator will re-enter from Phase 1. Research files in `cases/{project}/research/` survive.

## What never recovers automatically

- **Scraped sources that 404ed since the investigation started.** Check Wayback / Archive.today per `skills/web-archiving/SKILL.md`. If neither has a copy and the finding depends on it, the finding's confidence drops.
- **API keys that were leaked.** If you committed `.env` by mistake (it's gitignored by default, but still) rotate every key in that file immediately. The credit fraud from leaked Firecrawl/Anthropic keys can be expensive.

## Getting help

Before filing an issue:
1. `spotlight` preflight output — copy-paste the green/yellow/red table
2. Last 50 lines of `cases/{project}/data/investigation-log.json`
3. Any error messages from Terminal

File at https://github.com/buriedsignals/spotlight/issues
