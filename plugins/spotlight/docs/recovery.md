# Recovery — When Things Break Mid-Investigation

Agents crash, APIs rate-limit, your laptop closes the lid. Spotlight is designed around file-based state so recovery is cheap. This doc covers the common failure modes.

## The golden rule

**Resume only from the product resolver.** In Flue the launcher binds the
native capability to its active case, so call `spotlight_resolve({})`; Python
consumers import and call `spotlight_orchestration.resolve(CASE_DIR)`. The
optional CI/debug adapter is:

```text
python3 scripts/spotlight-orchestration.py status --json {CASE_DIR}
```

All three paths call the same importable authority. Resolution is read-only and
returns the authoritative phase, owner, requirements, attempt counts, and
resume detail. Transitions use a case-local portable lock and descriptor-
anchored no-follow replacement on macOS, Linux, and Windows through WSL; there
is no daemon or lock service.

## Failure scenarios

### Agent crashes mid-cycle

The investigator or fact-checker hit an iteration limit, an LLM error, or a tool timeout. The orchestrator's cycle loop catches this.

Recovery:
1. Check `{CASE_DIR}/data/investigation-log.json` — does the current cycle have an entry? If no, the investigator didn't complete; re-spawn it with the same methodology.
2. Check `{CASE_DIR}/data/findings.json` — is the `cycle` field equal to the current cycle? If no, partial write; the investigator spawn should merge with what's there.
3. Check `{CASE_DIR}/data/fact-check.json` — present and matching cycle? If no, re-spawn the fact-checker.
4. If both JSONs are intact for this cycle, proceed to readiness evaluation.

Re-spawning is safe. The investigator's EXECUTION prompt includes "merge with prior findings, do not duplicate" instructions. The fact-checker re-reads findings.json from scratch each time.

### Laptop sleeps / Terminal closes

No special handling is needed. Reopen the runtime and call
`spotlight_resolve({})` through its host-bound active-case capability. Invoke
only the returned `owner` and pass its `phase`, `missing`, `attempts`, and
`resume` detail unchanged. Do not infer recovery from artifact presence or
conversation memory.

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

First resolve the case. If the result returns `resume.state: requested` and
`resume.resume_at: ingest`, resume the idempotent ingest handler without asking
for confirmation again. If it returns `resume.state: completed` and
`resume.resume_at: seal`, do not repeat projection; call:

```text
spotlight_transition({
  operation: "decideIngest",
  payload: {decision: "completed"}
})
```

If the ingest handler reports a stale `.ingest-lock`:

1. Probe it with `python3 scripts/spotlight_safe.py destructive-probe --base {vault} --path .ingest-lock`.
2. Delete only the resolved lock path after the probe confirms containment.
3. Re-run the ingest handler only while the resolver still returns `requested`
   / `ingest`.

The handler is idempotent at the registry level, and resolver-owned state
prevents a completed projection from being invoked twice.

### Vault sync conflict (Obsidian Sync or other)

If two devices ingest simultaneously (rare), the `_registry.json` files may conflict. Resolution:

1. Accept the most-recent version as ground truth (usually the one with more entries).
2. Re-run ingestion from the case that was lost — it will fill in any missing notes.

### Review feedback follow-up is interrupted

Return the exported feedback to the Gate 1 owner. Validate its project and
target IDs, convert actionable content to bounded instructions, and record:

```text
spotlight_transition({
  operation: "requestFollowUp",
  payload: {instructions: "<targeted feedback instructions>"}
})
```

Resolve again. It must return `phase: execution` with the same instructions in
`resume`. When regenerated Gate 1 dependencies change, resolution returns Gate
1 approval for a new human decision. Feedback-file presence never determines
recovery.

### Corrupted case JSON

If a cycle wrote malformed JSON (e.g. the agent was interrupted mid-write):

1. Check `git log {CASE_DIR}/` if the case is under git (rare — `cases/` is gitignored by default).
2. Restore from a `.bak` if you kept one.
3. Otherwise: the safest recovery is to delete the corrupted file and re-spawn the agent that writes it. Findings / fact-check / investigation-log are all append/overwrite, not append-only.
4. If `findings.json` is lost entirely, start the cycle over — the investigator's research files in `{CASE_DIR}/research/` are still there, so the re-run is fast.

## Nuclear option: start fresh

Probe the path first: `python3 scripts/spotlight_safe.py destructive-probe --base cases --path {project}`

Backup the resolved case directory, then delete only the resolved `data/` directory after a second probe confirms it is inside that case. Re-run `spotlight`. The orchestrator will re-enter from Phase 1. Research files in `{CASE_DIR}/research/` survive.

## What never recovers automatically

- **Scraped sources that 404ed since the investigation started.** Check Wayback / Archive.today per `skills/web-archiving/SKILL.md`. If neither has a copy and the finding depends on it, the finding's confidence drops.
- **API keys that were leaked.** If you committed `.env` by mistake (it's gitignored by default, but still) rotate every key in that file immediately. The credit fraud from leaked Firecrawl/Anthropic keys can be expensive.

## Getting help

Before filing an issue:
1. `spotlight` preflight output — copy-paste the green/yellow/red/unconfigured table
2. Last 50 lines of `{CASE_DIR}/data/investigation-log.json`
3. Any error messages from Terminal

File at https://github.com/buriedsignals/spotlight/issues
