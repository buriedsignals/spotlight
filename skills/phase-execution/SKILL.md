---
name: phase-execution
description: Spotlight Phase 3 — Autonomous execution cycles (max 5): source-expression release mode resolution, investigator EXECUTION + fact-checker spawns, validators, editorial checks, monitoring recommendations, readiness criteria; includes the Stall Protocol
invocable_by: [orchestrator]
phase: execution
---

# Phase 3 — Execution (Autonomous Cycles, Max 5)

With approved methodology, begin the execution loop. No user involvement between cycles — decide autonomously.

## Source-expression release mode

Resolve this once before the first cycle and preserve it on every investigator
and fact-checker spawn:

1. If `data/case-contract.json` validates, findings use contract `1.1`, and
   `python3 scripts/validate-case.py {CASE_DIR}` passes, set
   `SOURCE_EXPRESSION_MODE: activated`.
2. Otherwise, set `SOURCE_EXPRESSION_MODE: pilot` only when the operator has
   explicitly selected this case for the source-expression pilot. Record or
   preserve a clean pre-pilot legacy bundle for later comparison/recovery.
3. In every other case, omit the field. This is the default and preserves
   findings contract `1.0`; do not create `data/source-expressions.json`.

Never infer activation from `source-expressions.json`, findings version, or a
migration audit alone. `data/case-contract.json` is the sole activation
authority. Pilot output is a side artifact and cannot be promoted in place by
the current migration command. Activation of a clean legacy case is a separate
operator-reviewed dry-run/apply flow:

```text
python3 scripts/migrate-source-expressions.py {CASE_DIR}
python3 scripts/migrate-source-expressions.py {CASE_DIR} --apply
python3 scripts/validate-case.py {CASE_DIR}
python3 scripts/validate-fact-check.py {CASE_DIR}
```

The checked-in comparison is
`docs/source-expression-pilot-results.json`. Its activation status is **NOT
APPROVED** because timed human review, correction yield, longitudinal locator
stability, and same-fixture migration effort remain unmeasured. Do not enable
`1.1` as the new-case default.

Expression validation proves exact-text, locator, hash, reference, lifecycle,
and status integrity. It does not prove truth, entailment, completeness, or
editorial fairness.

```
CYCLE N (N starts at 1):

  1. Spawn investigator (EXECUTION mode):

     handle = spawn-agent(
       agent_id: "investigator",
       prompt: "MODE: EXECUTION
PROJECT: {project}
PROFILE: {profile}
TIER: {config.model_tier}
CASE_ROOT: {CASE_ROOT}
CASE_DIR: {CASE_DIR}
{if source_expression_mode: SOURCE_EXPRESSION_MODE: {source_expression_mode}}
VAULT_PATH: {vault_path or 'none'}
INTEGRATIONS:
  osint_navigator_status={config.integrations.osint_navigator.status}
  osint_navigator_required={config.integrations.osint_navigator.required_in_phase_2}
  rlm_approved={methodology.rlm.approved}
  rlm_analysis_path={methodology.rlm.analysis_path or 'none'}
CYCLE: {N}
SKILLS: acquisition-graduation (graduate repeated dev-browser paths only after repeatability is proven), web-archiving (archive all evidence before citing), content-access (paywalled sources — use before marking inaccessible), epistemic-grounding (fill grounding object and cap confidence when support is weak), shell-safety (validate untrusted values before execute-shell), social-media-intelligence (social investigations), technical-investigation (technical leads)

ACQUISITION: Firecrawl first via search/fetch. After every Firecrawl result, run the missing-source gate. Use dev-browser when static acquisition is insufficient for dynamic pages, portals, downloads, screenshots, visual verification, forms, or legally appropriate authenticated/local-browser contexts.

{if N > 1: Previous findings gaps:
{gaps}

Fact-check gaps:
{fc_gaps}}

{if monitoring_units: Monitoring results since last cycle:
{monitoring_summary}}

When you identify targets worth persistent monitoring, add them to monitoring_recommendations[] in data/findings.json.

Read methodology from {CASE_DIR}/data/methodology.json.
If methodology.rlm.approved=true and data/rlm-analysis.json exists, read it as
lead-routing context only. Treat every RLM artifact as `needs_verification`;
do not cite RLM output as evidence.
Write to {CASE_DIR}/data/findings.json.
Write/update {CASE_DIR}/data/evidence-bundle.json with acquisition attempts, missing-source gate answers, artifact paths, hashes, and claim links.
Append to {CASE_DIR}/data/investigation-log.json.",
       config: { iteration_limit: 80 }
     )
     output = wait-agent(handle)

  2. When complete: read-file("{CASE_DIR}/data/findings.json"); verify investigation-log.json was appended.

  2.5. Validate the investigator output before fact-checking:

     ```
     execute-shell("python3 scripts/validate-case.py {CASE_DIR}")
     ```

     If validation reports errors (non-zero exit), the investigator left data
     bugs — empty `claim` fields, missing required keys, wrong-shape output,
     or dangling references. Do NOT proceed to the fact-checker. Re-spawn the
     investigator with the validator errors quoted verbatim in the prompt and
     a directive: "fix these data bugs without changing your findings or
     verdicts; only correct the shape." Loop until the validator passes.

  3. Spawn fact-checker:

     handle = spawn-agent(
       agent_id: "fact-checker",
       prompt: "PROJECT: {project}
PROFILE: {profile}
TIER: {config.model_tier}
CASE_ROOT: {CASE_ROOT}
CASE_DIR: {CASE_DIR}
{if source_expression_mode: SOURCE_EXPRESSION_MODE: {source_expression_mode}}
INTEGRATIONS:
  osint_navigator_status={config.integrations.osint_navigator.status}
  osint_navigator_required={config.integrations.osint_navigator.required_in_phase_2}
SKILLS: web-archiving (archive sources before issuing verdict), content-access (paywalled sources — use before marking inaccessible), epistemic-grounding (judge whether evidence actually grounds each claim), shell-safety (validate untrusted values before execute-shell), technical-investigation (technical claims)

Apply SIFT source credibility check before searching for corroborating evidence.
Independently assess claim-to-evidence grounding before assigning verdicts or confidence.
Archive every source before citing it. Work through the content-access hierarchy before marking any source inaccessible.
If you identify sources worth monitoring for ongoing verification, add them to monitoring_recommendations[] in data/findings.json.

Fact-check all claims in {CASE_DIR}/data/findings.json.
Read {CASE_DIR}/data/evidence-bundle.json when present and use it to assess acquisition quality, missing-source gates, screenshots/downloads, hashes, and human-verification flags.
Write to {CASE_DIR}/data/fact-check.json.",
       config: { iteration_limit: 50 }
     )
     output = wait-agent(handle)

  4. When complete: read-file("{CASE_DIR}/data/fact-check.json").

  4.5. Validate the fact-checker output before the editorial check:

     ```
     execute-shell("python3 scripts/validate-case.py {CASE_DIR}")
     execute-shell("python3 scripts/validate-fact-check.py {CASE_DIR}")
     ```

     If the structural validator fails, use the same shape-only correction rules
     as 2.5. If the evidence validator fails, re-spawn the fact-checker once with
     its reasons quoted verbatim: repair the named case-local path, line range,
     JSON Pointer, quote, or hash; otherwise downgrade the verdict and explain the
     gap. Never ask it to change prose merely to satisfy a language heuristic.
     Present Gate 1 only after the evidence validator passes, or explicitly disclose
     the remaining claim as unverified.

  5. Run editorial standards check:
     - Do findings have sources with URLs, timestamps, and `local_file`?
     - Does every finding include a `grounding` object with support type, source role, missing assumptions, and confidence cap?
     - Does evidence-bundle.json exist with acquisition method, artifact paths, missing-source gate answers, and claim links?
     - Does investigation-log.json have substance (techniques, queries, failed approaches)?
     - Do high-confidence findings have 2+ fact-check sources?
     - Do fact-check claims include `grounding_assessment`?
     - Are there findings with no fact-check verdict?
     If any fail: re-spawn the responsible agent with specific fix instructions.
     This counts as a cycle.

  5.5. Process monitoring recommendations:

     If data/findings.json contains monitoring_recommendations[]:

     1. Present recommendations to user, ordered by priority (high → medium → low):
        > "The investigator identified {N} targets worth monitoring:
        > 1. [HIGH] {target} — {rationale}
        > 2. [MEDIUM] {target} — {rationale}
        >
        > Approve, modify, or skip each?"

     2. For approved recommendations, invoke-skill("monitoring") to:
        - present a clear Mycroft handoff when durable monitoring is wanted,
        - otherwise retain the recommendation as case context.

        Spotlight never creates a Scoutpost project or scout, reads Scoutpost
        credentials, or records Scoutpost identifiers. Mycroft owns that
        optional integration after a separate explicit confirmation.

     3. Log all created monitor links to {CASE_DIR}/data/monitoring.json

  6. Evaluate readiness criteria (see `skills/spotlight/references/pipeline.md`):

     | Criterion | Threshold |
     |-----------|-----------|
     | Minimum findings | 3+ at high confidence |
     | Source independence | 2+ independent sources per key claim |
     | No unresolved disputes | 0 claims with "disputed" verdict and no resolution path |
     | Affected perspective | At least 1 finding from affected community/person |
     | Document trail | Primary source documents cited (not just news reports) |
     | Gap assessment | All gaps resolved or explicitly noted as limitations |

  7. If ALL criteria met: proceed to Gate 1 (`skills/phase-gate1`).

  8. If NOT met AND N < 5: identify specific gaps, increment N, loop.

  9. If NOT met AND N >= 5: trigger Stall Protocol.
```

---

# Stall Protocol

> "Investigation stalled after {N} cycles. Missing: {gaps}. Options: continue with more cycles, pivot angle, or review current findings as-is."

**STOP** and wait for the user's decision. Do not auto-advance.
