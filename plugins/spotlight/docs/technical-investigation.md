# Technical investigation

Spotlight's `technical-investigation` child skill adds passive technical research
methods to the existing investigation pipeline. Selected methods are adapted
from [CTI Expert](https://github.com/7onez/cti-expert) by Hieu Ngo /
chongluadao.vn (GitHub: 7onez), reviewed at commit
`f9ecc9b0258caff78d26c0b779d1687f4431749f`.

Spotlight does not install or invoke the upstream skill. The parent Spotlight
skill remains the orchestrator, current case files remain authoritative, and
the investigator and fact-checker retain separate sessions.

## Runtime shape

`technical-investigation` is a normal dynamically loaded child skill:

```text
spotlight parent
  -> investigator selects a technical method
  -> invoke-skill("technical-investigation")
  -> load one task reference for the configured model tier
  -> write existing Spotlight case artifacts
  -> independent fact-check
  -> optional verified-indicator export after Gate 1
```

There is no CTI pack manager, second agent hierarchy, upstream workspace, or
runtime-specific command surface. Codex and Claude load the child from the
Spotlight plugin. Flue/Pi discovers the same skill ID through the canonical
manifest-resolved skill store.

`harness/composition.json` lists the child for the orchestrator, investigator,
and fact-checker. This is dynamic-loading guidance, not an access-control
boundary.

## Included methods

| Need | Spotlight location | Treatment |
|---|---|---|
| Domain, IP, URL, or hash triage | `skills/technical-investigation/references/indicator-triage*.md` | Passive provider checks; labels remain leads. |
| DNS, certificate, registration, HTTP, and hosting history | `skills/technical-investigation/references/infrastructure-history*.md` | Dated observations with source and collection context. |
| Local document metadata and raw email headers | `skills/technical-investigation/references/document-message-forensics*.md` | Local-first analysis with originals and derived observations kept separate. |
| Public GitHub history | `skills/technical-investigation/references/github-public*.md` | Public repositories, commits, forks, tags, and releases only. |
| Public-ledger tracing | `skills/follow-the-money/references/blockchain-tracing.md` | Transaction-flow leads with provider-label and wallet-clustering limits. |
| Attempted, skipped, blocked, and null paths | `skills/investigate/references/coverage-discipline.md` | Coverage records due diligence; it does not score publishability. |
| Material contradictions | `skills/epistemic-grounding/SKILL.md` | Categories route follow-up checks; no trust scores or automatic winner. |
| JSON, CSV, and STIX indicator export | `scripts/export-verified-indicators.py` | Rewritten against Spotlight findings and fact-check contracts. |

The upstream runtime, installers, orchestration, workspace format, report stack,
and tool installation recipes are not shipped. Credential and breach searches,
stealer logs, secret scanning, active scanning, darknet collection, face and
phone searches, Wi-Fi geolocation, automatic identity merging, and numerical
trust or exposure scores are excluded.

## Model-tier loading

The model tier controls instruction density. It does not grant authority or
change evidence standards.

| Tier | Loaded instructions |
|---|---|
| `12b` | Root skill plus exactly one matching `*-compact.md` task reference. |
| `26b`, `31b` | Root skill plus one expanded task reference. |
| `frontier`, `api`, Codex Desktop, Claude Desktop | Root skill plus one expanded task reference unless a smaller tier is configured. |

Verified export uses one short reference at every tier. The manifest-floor
increase is approximately 99 tokens, the root stays below its 1,200-token
budget, and each compact reference is below 300 approximate tokens. Static
checks enforce these limits.

## Pipeline integration

### Methodology

The investigator invokes the child only when the brief contains a relevant
technical object or question. It records the chosen method in
`data/methodology.json`:

```json
{
  "skill_id": "technical-investigation",
  "method": "infrastructure-history",
  "upstream": "7onez/cti-expert",
  "active_sha": "f9ecc9b0258caff78d26c0b779d1687f4431749f",
  "reference": "infrastructure-history-compact.md"
}
```

`scripts/validate-methodology-navigator.py` requires new plans to use the
current reviewed CTI revision and checks that 12B plans select compact
references. Archival validation can pass `--allow-historical-cti`; cited SHAs
must still exist in `upstreams/cti-expert/reviewed-revisions.json`.

### Acquisition and findings

The selected reference uses Spotlight's existing verbs and integrations.
Technical observations follow the same evidence, archive, shell-safety, and
grounding rules as other findings.

Explicit export candidates live in `data/findings.json` under
`technical_indicators[]`. Each entry has an ID, linked finding ID, type, exact
value, context, and sources. Allowed types are IPv4, IPv6, domain, URL, MD5,
SHA-1, SHA-256, Bitcoin address, and Ethereum address. Personal selectors are
not part of this field.

### Independent fact-check

The fact-checker may invoke the same child but must collect independent evidence.
When a claim assesses an explicit indicator, the claim records its ID in
`technical_indicator_ids[]` and includes the exact, case-sensitive indicator
value in `claim_text`.

`scripts/validate-case.py` rejects unknown indicator IDs, mismatched finding
IDs, and claims that omit the exact value. An indicator attached to an otherwise
verified finding does not inherit that finding's verdict.

### Verified export

Export is offered only after Gate 1:

```bash
python3 scripts/export-verified-indicators.py \
  --findings CASE_DIR/data/findings.json \
  --fact-check CASE_DIR/data/fact-check.json \
  --format json \
  --output CASE_DIR/exports/verified-indicators.json
```

Use `csv` or `stix` for the other output formats. An indicator exports only when:

- a fact-check claim names its indicator ID;
- the claim uses the same finding ID and contains the exact value;
- that claim has verdict `verified`;
- every fact-check claim linked to the finding is also `verified`.

The exporter normalizes allowed values but preserves the original value. It
rejects URL credentials, queries, and fragments; neutralizes spreadsheet
formulas in CSV; generates deterministic STIX identifiers in Spotlight's own
namespace; and writes atomically inside the case directory.

## Source review and updates

The reviewed adaptation is reproducible from these records:

| Path | Purpose |
|---|---|
| `third_party/cti-expert/LICENSE` | Complete upstream license and addendum. |
| `third_party/cti-expert/SOURCE.json` | Author, repository, revision, and adaptation notice. |
| `upstreams/cti-expert/source.lock.json` | Active and acknowledged upstream SHAs. |
| `upstreams/cti-expert/reviewed-revisions.json` | Append-only reviewed SHAs and license digests. |
| `upstreams/cti-expert/source-map.json` | Upstream files mapped to each Spotlight adaptation. |
| `upstreams/cti-expert/review-policy.md` | Allowed subjects, exclusions, and promotion checks. |

`.github/workflows/cti-expert-upstream.yml` checks upstream daily. If upstream
HEAD differs, it opens or updates one `CTI Expert upstream review pending`
issue. The watcher does not download content into the skill tree, execute
upstream code, or advance `active_sha`.

A maintainer reviews a new revision by quarantining the source, checking the
license and mapped files, rewriting accepted changes into Spotlight, updating
the source records, and rebuilding the plugin. Online Spotlight installs and
updates receive the latest reviewed adaptation. Offline installs use the
bundled reviewed revision.

## Distribution and checks

The repository-root skill is canonical. Regenerate the Codex/Claude plugin
after changes:

```bash
python3 scripts/build-plugin-payload.py
python3 tests/plugin-distribution-check.py
```

Run the CTI-specific contracts and existing regression suite:

```bash
python3 tests/cti-upstream-check.py
python3 tests/technical-investigation-check.py
python3 tests/verified-indicator-export-check.py
python3 tests/methodology-navigator-check.py
python3 tests/validate-case-check.py
python3 tests/skills-manifest-check.py
python3 harness/validate_composition.py
bash tests/eval.sh
```

Before release, capture one 12B trace showing that only one compact reference
loaded and one Codex or Claude Desktop trace showing expanded-reference routing.
Do not run either test against an active model-validation session.

## Credit and license

Selected methods are adapted from
[CTI Expert](https://github.com/7onez/cti-expert) by Hieu Ngo /
chongluadao.vn (GitHub: 7onez), under the MIT License with the upstream Ethical
Use Addendum. Spotlight's changes are substantial, and no affiliation or
endorsement is implied. See `NOTICE.md` and
`third_party/cti-expert/LICENSE` for the full record.
