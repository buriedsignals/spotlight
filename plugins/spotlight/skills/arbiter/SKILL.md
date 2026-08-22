---
name: arbiter
description: Use the native Arbiter HTTPS API to browse or create bounded social-media case studies and attach its analytics to a Spotlight report.
version: "1.0"
invocable_by: [investigator, fact-checker, orchestrator, user]
requires: [integrations]
---

# Arbiter

Use this skill when the operator invokes `/arbiter` or asks Spotlight for an
Arbiter case study, archived social posts, entity stance, themes,
actor/community analysis, or Arbiter report visuals.

Arbiter is remote and disabled in sensitive mode. Previously saved Arbiter JSON
may still be rendered offline.

Each member uses their own, member-owned Arbiter API key. There is no shared Spotlight,
Buried Signals, or hosted key. Register through the [Indicator partner signup
link](https://arbiter.simppl.org/auth/register?eventSignup=5cce20c609334e538f07127322361862e3136e3d-324a-4c60-894a-5f42d2d57f8a),
create the key in the member's Arbiter account, and configure it locally as
`ARBITER_API_KEY`. An optional `ARBITER_API_BASE` must match the deployment that
minted the key. Never ask the operator to paste a key into chat, a case file,
or a command, and never request or log key material.

## Start

Run the integration preflight and require Arbiter to be green:

```bash
python3 integrations/preflight.py --json
```

The Arbiter preflight performs an unauthenticated direct OpenAPI smoke against
`https://arbiter.simppl.org/api/v1/openapi.json`, or against the validated
`ARBITER_API_BASE` override when one is configured. Missing
`ARBITER_API_KEY` is local configuration state, not a reason to disclose
credentials. Read `integrations/arbiter/integration.md` and
`docs/arbiter-api.md` before making a request.

## Prompt

When the API is available, ask whether the operator wants to:

1. Browse existing studies and match one to the investigation; or
2. Create a study through the reviewed create → search-plan → human review →
   finalize workflow.

Follow `integrations/arbiter/integration.md` exactly. It is the source of truth
for direct endpoint shapes, credit disclosures, confirmation gates, timeout and
retry behavior, evidence rules, sensitive-mode blocking, and polling.

Every authenticated call uses the in-process native client:
`ArbiterClient.from_env()` reads the member-owned `ARBITER_API_KEY` and sends
`Authorization: Bearer <member-key>` over HTTPS. Build a file-backed JSON
`input-file`, validate `case_study_id` and `post_id`, and write the unmodified
raw response to an `output` file. Use `safe_research_path()` for contained
regular files under `{CASE_DIR}/research`; never accept traversal, symlink
parents, or unsafe slugs. Never interpolate untrusted text into shell
commands, and do not use shell or curl transport for API calls. Preserve
unknown upstream fields and keep request/response files beside one another in
`{CASE_DIR}/research/`.

Local human review and explicit confirmation remain required before charged or
external-state-changing operations. `confirmed` is not a wire field: never
send `confirmed: true` in create or finalize JSON. The local gate is not
authorization from the API.

For a report, save the raw `GET /topics/{case_study_id}/report` response under
`{CASE_DIR}/research/arbiter-report-<slug>-<timestamp>.json`. That filename and
response shape activate Spotlight's deterministic Arbiter appendix. Use
`GET /topics?limit=100` with query parameters, never a JSON GET body.
