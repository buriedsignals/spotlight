# Harness notes for CTI readiness: three fixes and deferred options

**Status:** scoped handoff; no implementation included
**Date:** 2026-07-10
**Baseline:** Spotlight `f15817b2` plus an active harness/v4-validation worktree
**Related PRD:** [cti-expert-integration-prd.md](cti-expert-integration-prd.md)

## Read this first

This is **not** a defect report against Spotlight's D1/D2 architecture and is
not a request to build a capability-enforcement subsystem now.

The current design intentionally makes installed skills discoverable by name,
loads bodies only on invocation, and relies on parent/role instructions to
compose them. That is what keeps the same Spotlight parent skill portable across
Codex, Claude, and Flue/Pi.

The current model-tier rules also serve context efficiency:

- the 12B `.raw` prohibition avoids context rot;
- optional integration dismissal protects its context and tool-selection
  budget;
- these policies were not presented as security or authorization boundaries.

The CTI review surfaced three small corrections worth adopting and several
larger ideas that are explicitly deferred.

## Current completion model

Spotlight already has typed phase state and deterministic validation:

- `methodology.json` records the approved plan;
- `findings.json` records claims and evidence;
- `fact-check.json` records independent verdicts;
- validators reject structurally or evidentially invalid artifacts;
- the gate presentation is deliberately human-facing prose.

An empty final response is a demonstrated 12B failure mode, not the normal
completion path. The auto-nudge is a recovery guard, and the v4 tune is
currently validating this behavior. Do not add a typed gate-transition envelope
or otherwise change completion semantics during that run.

## Sequencing rule

Finish the active v4 validation and open-tier ship first. Re-read the named files
against the resulting baseline before editing because this worktree is moving.

After that stable point, consider the following three fixes as small independent
changes. Do not combine them with CTI content.

## Fix 1 — remove delegation from worker toolsets

### Current behavior

[spotlight.ts](../harness/flue/src/agents/spotlight.ts) exposes the Flue `task`
tool to child profiles, while `WORKER_GUARD` instructs investigator and
fact-checker never to use it.

This guidance is usually effective, but worker re-delegation has caused
`task -> task -> task` recursion and `DelegationDepthExceededError`. Unlike the
broader skill-isolation proposal, this has a demonstrated correctness cost.

### Desired change

If Flue supports per-profile tool filtering, remove `task` from investigator and
fact-checker profiles while retaining it for the orchestrator. Keep
`WORKER_GUARD` as defense in depth and as portable guidance for runtimes that do
not expose an equivalent filter.

If the current Flue API cannot filter tools per profile, stop at a focused
feasibility note. Do not introduce a capability resolver or role-specific skill
tree merely to solve this one issue.

### Acceptance

- Orchestrator delegation still works.
- Investigator and fact-checker cannot call `task` on the Flue path when the API
  supports filtering.
- Existing child sessions, compaction, file access, and return values are
  unchanged.
- A regression fixture proves the prior recursive path cannot start.

## Fix 2 — preserve model tier in mid-run preflight

### Current behavior

[skills/integrations/SKILL.md](../skills/integrations/SKILL.md) documents this
mid-run call:

```text
python3 integrations/preflight.py --json
```

The parent Spotlight skill passes `--model-tier {config.model_tier}`. The bare
child-skill call can therefore produce an integration status inconsistent with
the session's configured efficiency tier.

### Desired change

Pass the same model-tier value in the mid-run call:

```text
python3 integrations/preflight.py --model-tier {config.model_tier} --json
```

This preserves one efficiency policy throughout the session. It does not turn
preflight into an access-control mechanism.

### Acceptance

- A 12B mid-run check retains the intended dismissed-integration view.
- 26B, 31B, frontier, and API checks retain their configured behavior.
- Parent and child instructions use the same call shape.
- Plugin payload regeneration carries the corrected skill text.

## Fix 3 — use the canonical skill store for Flue discovery

### Current behavior

[install-spotlight.sh](../install-spotlight.sh) creates the canonical
manifest-resolved skill store, but the Flue setup currently links
`.agents/skills` to the repository's entire `skills/` directory.

This is not evidence that D1/D2 dynamic loading is wrong. It is a placement
inconsistency: the catalog/manifest already chooses the installed set, but Flue
does not consume that set.

### Desired change

Point Flue's discovery link at the canonical Spotlight skill store. Continue to
make every skill in that resolved set dynamically discoverable; do not create
per-role stores or hide skill descriptions.

### Acceptance

- Flue discovers every ID in `skills.manifest` through the canonical store.
- A skill deliberately omitted from the manifest is not discovered through the
  Flue workspace link.
- Codex/Claude plugin behavior is unchanged.
- Development and installed-path smoke tests still load the parent and invoke a
  child skill.
- User-owned files are untouched.

## Explicitly deferred

Do not put these on the current critical path:

- fail-closed model/role/egress capability resolution;
- immutable session capability snapshots;
- signed model-specific physical skill variants;
- `spotlight capabilities list|enable|disable|sync`;
- a generic network/egress broker;
- per-role skill isolation;
- typed gate-transition envelopes.

They may be sensible if Spotlight later ships several independently updated
third-party capability families or needs contractual hard isolation. The first
CTI child skill does not justify that multi-week, cross-repository program.

## Preserved design invariants

All three fixes must preserve:

- one Spotlight parent skill across Codex, Claude, and Flue/Pi;
- dynamic `invoke-skill` composition;
- all resolved child skills discoverable by description;
- skill bodies loaded only when invoked;
- model-tier guidance as context-efficiency tuning;
- existing JSON phase artifacts and validators;
- human-facing gate prose and explicit user approval;
- investigator/fact-checker session separation;
- existing evidence and case workspace contracts.

## Non-goals

- Importing CTI Expert.
- Redesigning `composition.json` into an access-control manifest.
- Reclassifying documented efficiency behavior as a security promise.
- Changing model prompts or completion behavior while v4 is under validation.
- Expanding the installer or engine beyond the three named seams.

## Likely touchpoints

- [harness/flue/src/agents/spotlight.ts](../harness/flue/src/agents/spotlight.ts)
- [skills/integrations/SKILL.md](../skills/integrations/SKILL.md)
- [install-spotlight.sh](../install-spotlight.sh)
- [skills.manifest](../skills.manifest)
- [plugin payload builder](../scripts/build-plugin-payload.py)
- relevant harness, installer, manifest, and plugin-distribution tests

No change should be made from the line references alone; verify the active
post-v4 code first.

---

## Resolution status (2026-07-10, follow-up session)

Three near-term items from this doc's gap table were dispositioned ahead of the
full H1–H9 program:

1. **Worker `task` tool removal (H4)** — **not implementable in `@flue/runtime`
   1.0.0-beta.9.** `AgentProfile.tools` covers custom tools only; there is no
   per-profile control over built-in capabilities, and tool-name shadowing is a
   definition-time error ("must be unique across active built-in and custom
   tools"). Note `task()` *without* an agent name spawns a child with the
   parent's full config (docs/guide/subagents.md), so even `subagents: []`
   does not close the recursion vector. The runtime's existing mechanical guard
   is the delegation depth cap (`DelegationDepthExceededError`). Filed as an
   upstream feature request; the `WORKER_GUARD` prose remains the interim
   mitigation.
2. **Bare mid-run preflight (H5/H7)** — **fixed.** `skills/integrations/SKILL.md`
   (and the plugin mirror) now invoke
   `integrations/preflight.py --model-tier {config.model_tier} --json`.
3. **Flue discovers the whole `skills/` tree (H3)** — **fixed.**
   `install-spotlight.sh` now builds `.agents/skills` as per-skill symlinks
   scoped to `skills.manifest` (with managed-link pruning) instead of one
   symlink to the whole tree; the dev checkout was regenerated to the same
   shape. The manifest is now the actual Flue discovery boundary.
