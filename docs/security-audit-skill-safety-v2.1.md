# Skill Safety Audit v2.1

Audit date: 2026-06-01

SkillSpector version: 2.0.0

## Commands

Static scans were run with:

```bash
uv run --python /opt/homebrew/bin/python3.13 \
  --project kit/skillspector/upstream \
  skillspector scan <target> \
  --no-llm \
  --format json \
  --output /tmp/spotlight-skillspector-audit/<target>.json
```

Scanned targets:

- `tools/spotlight/AGENTS.md`
- `tools/spotlight/agents/`
- each `tools/spotlight/skills/*/SKILL.md`
- each full `tools/spotlight/skills/*/` directory
- aggregate `tools/spotlight/skills/`

Raw reports were written to `/tmp/spotlight-skillspector-audit/` and are not committed.

## Verdict

Spotlight is safe enough to keep using with documented caveats. The scan found no evidence of malicious skill behavior, credential harvesting, stealth persistence, or prompt-injection instructions in the core orchestrator, agent contract, `epistemic-grounding`, `review`, or `spotlight` skill files.

The main real hardening issue was `report-drafting/references/report-template.html`, which used HTML comments for agent-facing template instructions. v2.1 removes those hidden comments from the template and keeps agent instructions in `report-drafting/SKILL.md`.

## Summary

| Target | Severity | Interpretation | Action |
|---|---|---|---|
| `AGENTS.md` | LOW | Clean | None |
| `agents/` | LOW | WHOIS guidance is intentional but should avoid full case context | Track helper-wrapper follow-up |
| `skills/content-access/` | MEDIUM | Intentional external API calls | Added data-minimization boundary |
| `skills/osint/` | MEDIUM | Intentional Navigator API calls | Added minimal-payload and sensitive-mode boundary |
| `skills/report-drafting/` | MEDIUM | Real hidden-instruction pattern in HTML comments | Removed HTML comments from template |
| `skills/web-archiving/` | LOW | Intentional external archiving behavior | Covered by shell-safety; future sensitive-mode wording can tighten |
| aggregate `skills/` | CRITICAL | Inflated by cumulative medium findings and template comments | Use per-directory triage, not aggregate install verdict |

## False Positives

- `skills/shell-safety/SKILL.md`: unsafe curl text is an anti-example, not an instruction.
- `skills/investigate/references/platform-techniques.md`: "External tool" labels describe websites/services, not unrestricted tool grants.
- `skills/investigate/references/verification-methods.md`: "Detection tool" labels describe external services.
- `skills/social-media-intelligence/SKILL.md`: "without verification" describes a media failure mode, not Spotlight behavior.

## Accepted Capabilities

Spotlight intentionally supports external acquisition services for OSINT work: OSINT Navigator, Unpaywall, CORE, Semantic Scholar, WHOIS-like lookups, web archives, browser acquisition, and monitoring integrations. These capabilities remain acceptable only within the verb contract, shell-safety rules, preflight status checks, and sensitive-mode constraints.

## Hardening Backlog

- Route WHOIS lookup guidance through an integration/helper wrapper rather than inline API guidance in `agents/fact-checker.md`.
- Add a future regression that scans `.html` templates for hidden agent instructions.
- Tighten web-archiving sensitive-mode wording so public archiving is disabled unless the user explicitly asks.
- Optionally rename noisy "External tool" headings in investigate references if future scans remain noisy.
