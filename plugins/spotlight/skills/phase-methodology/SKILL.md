---
name: phase-methodology
description: Spotlight Phases 1–2 — Brief and Methodology: user brief conversation with approval gate, then investigator PLANNING spawn, methodology validation, RLM proposal (frontier opt-in), methodology approval gate
invocable_by: [orchestrator]
phase: brief,methodology
---

# Phase 1 — Brief (Skill <-> User)

This is a conversation between you and the user. Do NOT spawn agents.

1. **If the lead includes a URL**, scrape it first:
   ```
   fetch(url="<URL>", output_path="{CASE_DIR}/research/lead-source.md")
   ```
   Then `read-file("{CASE_DIR}/research/lead-source.md")` to understand the source material.

2. Restate the lead in one sentence.

3. Ask 1–3 clarifying questions if scope, angle, or priority is unclear. Keep it tight — the investigator agent handles planning, not you.

4. Summarize the agreed direction in a few sentences.

5. **Gate: user approves the brief direction.**

6. Write the approved direction: `write-file("{CASE_DIR}/brief-directions.txt", <directions>)`.

---

# Phase 2 — Methodology (Skill -> Agent -> User)

After brief approval, spawn the investigator in PLANNING mode:

```
handle = spawn-agent(
  agent_id: "investigator",
  prompt: "MODE: PLANNING
PROJECT: {project}
PROFILE: {profile}
TIER: {config.model_tier}
CASE_ROOT: {CASE_ROOT}
CASE_DIR: {CASE_DIR}
VAULT_PATH: {vault_path or 'none'}
INTEGRATIONS:
  osint_navigator_status={config.integrations.osint_navigator.status}
  osint_navigator_required={config.integrations.osint_navigator.required_in_phase_2}
SKILLS: integrations, osint, investigate, epistemic-grounding, acquisition-graduation, web-archiving, content-access, shell-safety, social-media-intelligence (social investigations), technical-investigation (technical leads)

NAVIGATOR ROUTING (CLI-first — make one independent tool decision per direction):

If osint_navigator_required=true (subscription / entitled deployments), before writing methodology.json:
1. invoke-skill("integrations")
2. invoke-skill("osint")
3. invoke-skill("navigator")
4. run `navigator tools find "<need>" --json` and inspect chosen tools with `navigator tools show`
5. record CLI/API mode, catalog ID/version or retrieval time, non-secret parameters, warnings, and source URLs
In sensitive/offline mode make no Navigator request; record OSINT discovery as policy-skipped and use allowed local fallbacks.

If osint_navigator_required=false (local / open-weights deployments — no Navigator entitlement), discover tools from the LOCAL index — no external call, no mandatory reads:
- execute-shell("python3 scripts/osint-tools.py find \"<lead-derived keywords>\" [--category <cat>] [--limit 8]")
  Derive keywords from the lead = entity type + geography + task. Scope with --category when the direction is clear (`python3 scripts/osint-tools.py categories` lists them). Examples:
    Swiss foundation leadership  ->  find "switzerland swiss zug registry" --category public_records   ;   find "people directory" --category people
    crypto wallet tracing        ->  find "ethereum wallet blockchain" --category cryptocurrency
- list the tools you chose in methodology.json tools_required[]; set navigator:{required:false, used:false, fallback_reason:"local osint-tools index (no Navigator entitlement)"} — or omit the navigator block entirely.

Approved brief directions:
{directions}

You may recommend monitoring targets in your methodology (see skills/monitoring for the recommendation schema and external-monitor lifecycle).
If the investigation involves social media, plan to invoke social-media-intelligence for account authenticity and coordination detection.

Write methodology to {CASE_DIR}/data/methodology.json.
Include skills_invoked[] and the navigator block required by schemas/methodology.schema.json.
Do NOT execute the investigation.",
  config: { iteration_limit: 80 }
)
output = wait-agent(handle)
```

When the agent completes:

1. `read-file("{CASE_DIR}/data/methodology.json")`
2. Run the methodology gate (tier-aware — the validator enforces the Navigator contract ONLY when osint_navigator_required=true; on the open tier it just checks the navigator block is absent or consistent):

   ```
   execute-shell("python3 scripts/validate-methodology-navigator.py {CASE_DIR} --config .spotlight-config.json")
   ```

   If validation fails, do not present the methodology for approval. Re-spawn or
   re-prompt the investigator with the fix the validator prints:

   > (Navigator entitled) "methodology.json does not show a CLI-first Navigator decision. Record tool and data-source decisions, provenance, and any policy/entitlement skip."
   > (local / open tier) "Fix the navigator block per the validator: set navigator:{required:false, used:false, fallback_reason:...} or omit it, and ensure tools_required[] lists the tools osint-tools returned."

3. Present a summary of the proposed methodology to the user
4. **Tier split (read first):** on the **local / Pi / non-frontier harness**, the RLM is **default-on and auto-run per research cycle without a user-approval gate** — see the runtime adapter; it benchmarks better on small models, which need the context reduction. The proposal/approval flow in this step applies to **interactive cloud/frontier setups only** (where RLM is opt-in). If you are running autonomously (no user to ask), do not propose — just run RLM per the adapter and continue.

   If `.spotlight-config.json` has `integrations.rlm.enabled=true` (frontier opt-in), propose
   RLM as a methodology-phase option before the approval gate. Use this exact
   decision boundary:

   - If RLM mode is `lite`, propose deterministic RLM.
   - If RLM mode is `local_gemma4_e4b` and `integrations.rlm.preflight_status`
     is `green`, propose hybrid Gemma4 E4B RLM.
   - If RLM mode is `local_gemma4_e4b` but preflight is `yellow` or `red`, say
     the configured RLM is unavailable and skip without blocking methodology.
   - If RLM is not enabled/configured, skip this check silently.

   Proposal wording:

   > "RLM is installed for this Spotlight setup. Benchmarks on the synthetic
   > context-rot suite improved average recall from 0.75 to 1.0, removed decoy
   > hits from 4 to 0, and cut average downstream lines from 10.25 to 3.0 with
   > hybrid Gemma4 E4B. It adds a short local analysis pass and produces
   > `data/rlm-analysis.json` as leads only, never verified facts. Use RLM for
   > this methodology?"

   If the user approves, write/update `methodology.json` with:

   ```json
   {
     "rlm": {
       "available": true,
       "proposed": true,
       "approved": true,
       "mode": "lite|local_gemma4_e4b",
       "model": "gemma4:e4b|null",
       "prefilter": true,
       "hybrid": true,
       "decision_at": "<ISO timestamp>",
       "decision_by": "user",
       "run_id": "<timestamp>-rlm",
       "request_path": "{CASE_DIR}/data/rlm-request.json",
       "analysis_path": "{CASE_DIR}/data/rlm-analysis.json",
       "audit_path": "docs/rlm-benchmark-audit.md",
       "evidence_boundary": "lead-only; never verified or publishable"
     }
   }
   ```

   Then write `{CASE_DIR}/data/rlm-request.json` with the chosen mode,
   model, prefilter/hybrid flags, and a `corpus_paths` list containing
   case-contained text/JSON/Markdown files already created for the lead,
   brief, methodology, and saved source material. Run:

   ```
   execute-shell("python3 integrations/rlm/run_rlm.py {CASE_DIR}/data/rlm-request.json")
   ```

   If the user declines, write `rlm.available=true`, `proposed=true`,
   `approved=false`, `mode="off"`, and a short `declined_reason`.

   If RLM was configured but unavailable, write `rlm.available=false`,
   `proposed=false`, `approved=false`, `mode="off"`, and a concrete
   `skip_reason`. Do not block methodology approval.

5. **Gate: user approves the methodology.** Iterate if the user has changes.
   Re-run the methodology validator after the final change. When the user
   explicitly approves, persist the attributable, current-input receipt:

   ```text
   python3 scripts/spotlight-orchestration.py approve methodology --approved-by "<human identity>" --approved-at "<ISO 8601 timestamp>" {CASE_DIR}
   ```

   The receipt binds the current `brief-directions.txt` and
   `data/methodology.json`. The methodology file alone remains a draft; if
   either input changes, status returns `methodology_approval` again.
   If the methodology changes after an approved RLM run, update
   `methodology.json`, regenerate `rlm-request.json` with the changed
   methodology/corpus paths, rerun RLM, revalidate, and record a new approval
   before Phase 3.
6. After approval and before Phase 3 research begins, remind the user:

   > **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.
