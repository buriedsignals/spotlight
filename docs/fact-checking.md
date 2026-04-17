# Fact-Checking

The fact-checker is an independent verification pass. It runs after every investigator cycle with its own context, spawned separately, reading only the investigator's JSON output — not their reasoning. The goal: stress-test every factual claim against available evidence.

## Independence

The fact-checker is **spawned**, not invoked inline. It receives a fresh context:

- It reads `cases/{project}/data/findings.json` — the investigator's structured output
- It does **not** receive the investigator's reasoning trace, methodology doc, or unfiltered scratch work
- It does **not** share memory with the investigator
- It **cannot** `spawn-agent` (the `disallowed_verbs: [spawn-agent]` rule prevents recursive spawning)

This matters: if the investigator and fact-checker shared context, the fact-checker would inherit priors and biases. The independence rule forces each claim to survive verification without the narrative weight of how it was discovered.

## SIFT — the source credibility pass

Before searching for corroborating evidence, the fact-checker evaluates source credibility using **SIFT** (Michael Caulfield's methodology):

| Step | Question | What to do |
|---|---|---|
| **S** — Stop | Does this source warrant trust at face value? | Note red flags: anonymous authorship, recent domain registration, sensationalist framing |
| **I** — Investigate the source | Who runs it? What's their track record? | Read the About page, named authors, institutional affiliations. `execute-shell('curl https://api.whois.vu/?q={domain}')` to check domain age if needed |
| **F** — Find better coverage | Is this original or secondary? | A news article citing a study is secondary. Trace claims to their origin |
| **T** — Trace claims back | Are quotes attributed correctly? Do linked sources say what's claimed? | Follow the chain |

SIFT runs before evidence search so the fact-checker doesn't multiply unreliable sources.

### By claim type

| Claim type | Additional check |
|---|---|
| Image-based | Reverse image search (`invoke-skill("osint")` → TinEye / Yandex / Google Lens routing) |
| Social media | Account age < 6 months or sudden follower spikes = low reliability. `invoke-skill("social-media-intelligence")` for coordination detection |
| Document | Metadata check via `execute-shell("exiftool {file}")` if the file is local |
| Financial | `invoke-skill("follow-the-money")` — cross-check against registries, offshore leaks |

## Evidence search

For each claim the fact-checker searches for:

- **Corroborating evidence** — independent sources that support the claim
- **Contradicting evidence** — sources that dispute or undermine the claim

Both must be sought. Do not stop at the first agreeing source. This is structural — the finder bias only gets neutralized if you actively look for disconfirmation.

### Source handling

- **Archive each source** before citing (`invoke-skill("web-archiving")`) — Wayback → Archive.today → local
- **Paywalled sources** — `invoke-skill("content-access")` and work through the 8-step hierarchy before marking `inaccessible`
- **Weight by source type**:
  - Primary (court filings, direct documents, testimony) > secondary (news reports, analysis)
  - Current evidence > potentially outdated evidence
  - Full-text access > abstract-only

## Verdict taxonomy

Every claim gets one of four verdicts. Precision matters — conflating these is a critical failure.

| Verdict | Definition | Required evidence |
|---|---|---|
| `verified` | True per the available evidence | 2+ independent reliable sources with no credible contradicting evidence |
| `unverified` | Evidence is silent | Thorough search produced neither supporting nor contradicting reliable sources. **This is not "false."** It means the evidentiary record does not speak to the claim |
| `disputed` | Reliable evidence exists both for and against | Document both sides with equal weight |
| `false` | Evidence actively contradicts the claim | Strong evidence from reliable sources directly contradicts what was claimed |

### The `unverified` vs `false` distinction

The most common failure mode is collapsing `unverified` into `false`. They are different:

- `unverified` = "we looked and the record is silent on this"
- `false` = "we looked and strong evidence says the claim is wrong"

If a claim is about a secret offshore structure and no leak has surfaced, the verdict is `unverified`, not `false`. The claim might still be true; the evidence just isn't public.

### Unfalsifiable claims

Predictions, opinions, and vague statements can't be fact-checked. Mark these as `not_checkable` in the `notes` field instead of forcing a verdict.

## Evidence trail

Every claim produces an evidence trail in `fact-check.json`:

```json
{
  "id": 1,
  "finding_id": "F1",
  "claim_text": "Company X paid Y million to Z in 2024",
  "verdict": "verified",
  "confidence": "high",
  "evidence_for": [
    {
      "description": "Contract document on gov.us registry filed 2024-06-15",
      "source": "https://sam.gov/opp/abc123",
      "source_type": "primary",
      "archive_url": "https://web.archive.org/web/20260315/https://sam.gov/...",
      "access_method": "full_text"
    },
    {
      "description": "Reuters coverage confirming the contract award",
      "source": "https://reuters.com/...",
      "source_type": "secondary",
      "archive_url": "https://web.archive.org/web/...",
      "access_method": "full_text"
    }
  ],
  "evidence_against": [],
  "sources": ["https://sam.gov/opp/abc123", "https://reuters.com/..."],
  "notes": "Primary registry entry validated via secondary press. No contradicting evidence found."
}
```

### Why both sides are recorded

Even `verified` claims record any weaker contradicting evidence if found. This is transparency — the reader can see that the fact-checker looked and what they found. A verdict without counter-evidence documented is less credible than one that acknowledges the weaker counter-signals and explains why the verdict still holds.

## access_method and confidence caps

The `access_method` on each source determines the maximum confidence a claim can carry:

| access_method | Confidence cap |
|---|---|
| `full_text`, `open_access`, `free_version`, `author_provided`, `institutional_access`, `preprint` | No cap |
| `archive_copy`, `cached_copy` | No cap (note snapshot date in `access_notes`) |
| `reader_mode`, `partial_text` | `medium` max |
| `abstract_only` | `medium` max (abstract may omit key findings) |
| `author_request_pending` | `low` — pending, do not cite as verified |
| `inaccessible` | `low` — cite the source but flag that content was not verified |

A finding built on `inaccessible` sources cannot be `high` confidence. This is hardcoded into the readiness criteria.

## Summary stats

`fact-check.json` carries a summary block:

```json
{
  "summary": {
    "total_claims": 12,
    "verified": 7,
    "unverified": 3,
    "disputed": 1,
    "false": 1
  }
}
```

This rolls up into the Gate 1 readiness check. One of the six readiness criteria is "No unresolved disputes" — a `disputed` verdict without a resolution path fails that criterion.

## gaps_for_next_cycle

If a claim is `unverified` or `disputed`, the fact-checker can suggest what the next investigation cycle should try:

```json
{
  "gaps_for_next_cycle": [
    "F3 — need second independent source for funding amount (only one registry record found)",
    "F7 — interview transcript cited but not archived; scrape before cycle 2"
  ]
}
```

The orchestrator reads these gaps and includes them in the next-cycle investigator prompt under "Previous fact-check gaps". This closes the loop: the fact-checker shapes the next investigation pass.

## Monitoring recommendations from the fact-checker

The fact-checker can add `monitoring_recommendations[]` to `findings.json` when it identifies sources worth ongoing tracking. Examples:

- A government page that may publish updated statistics relevant to a disputed claim
- A social account that may retract or update a statement flagged as unverified
- A news source in a specific location covering developments that could confirm or contradict findings

Same recommendation schema as the investigator (see `skills/monitoring/references/recommendation-schema.md`). The orchestrator processes these identically.

## When the fact-checker is spawned

The orchestrator spawns the fact-checker at the end of every cycle, after reading the investigator's `findings.json`:

```
spawn-agent(
  agent_id: "fact-checker",
  prompt: "PROJECT: {project}
INTEGRATIONS: osint_navigator={config.integrations.osint_navigator}
SKILLS: web-archiving, content-access

Fact-check all claims in cases/{project}/data/findings.json.
Write to cases/{project}/data/fact-check.json.",
  config: { iteration_limit: 50 }
)
```

Per-runtime implementations of `spawn-agent` / `wait-agent` are documented in [integrations.md](integrations.md).

### Editorial standards check (after fact-checker)

After the fact-checker writes `fact-check.json`, the orchestrator runs an editorial standards check:

1. Do findings have sources with URLs, timestamps, and `local_file`?
2. Does `investigation-log.json` have substance (techniques, queries, failed approaches)?
3. Do high-confidence findings have 2+ fact-check sources?
4. Are there findings with no fact-check verdict?

If any fail, the responsible agent is re-spawned with specific fix instructions. This counts as a cycle.

## Sensitive mode

In sensitive mode:

- `fetch` / `search` stripped from the fact-checker's `allowed_verbs`
- The fact-checker works from the investigator's pre-scraped `cases/{project}/research/`
- Verdicts are explicitly flagged as **sensitive-mode constrained** when evidence gathering was limited
- `gaps_for_next_cycle` notes which gaps require sensitive-mode exit to resolve

## Rules

- **Never assume truth without evidence.** Plausible ≠ verified.
- **Always present both sides.** Even for `verified` claims, note any weaker contradicting evidence.
- **Distinguish `unverified` from `false` precisely.** Unverified = silent record. False = contradicted by evidence.
- **Do not editorialize.** Verdicts are about factual accuracy, not importance or moral significance.
- **Quote sources verbatim** where possible. Paraphrasing introduces distortion.
- **Flag unfalsifiable claims.** Predictions, opinions, vague statements — note as `not_checkable` rather than forcing a verdict.
- **Link back to findings.** Use `finding_id` to connect each claim to its source finding.
