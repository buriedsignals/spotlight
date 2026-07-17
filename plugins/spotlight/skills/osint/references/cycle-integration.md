# Investigation Cycle Integration

How and when to use the unified Navigator skill during Spotlight investigation cycles.

---

## Availability Check

Your spawn prompt includes an `INTEGRATIONS` line. If Navigator is green and
the case is not sensitive/offline, load `navigator` and use its CLI. If it is
absent, capability-limited, or policy-disabled, record the reason and use the
curated local tool list where permitted. Do not make a raw health/API request
or expose an authentication token.

---

## PLANNING Mode (Investigator)

Query Navigator to inform your methodology. For each investigation direction:

1. Identify what type of OSINT task each step requires (corporate lookup, image verification, etc.)
2. Run `navigator tools find "<need>" --json`, then inspect a candidate with
   `navigator tools show <tool-id>` before choosing it.
3. Independently run `navigator data find "<structured records need>" --json`
   and inspect a candidate with `navigator data show <source-id>` where a
   reproducible public-data source may fit.
4. Record selected tools and the data-source decision (including a concise skip
   reason) per direction. Preserve catalog ID/version or retrieval time.

---

## EXECUTION Mode (Investigator)

### At cycle start
Query Navigator for any tools needed by the approved methodology that you don't already know how to use.

### Mid-cycle (when hitting a wall)
If a planned technique fails or a new line of inquiry opens, repeat the CLI
discovery loop. Run `navigator query <source-id> ...` only after inspecting the
source playbook and saving its structured output under `{CASE_DIR}/research/`.

Record tool discoveries in investigation-log.json:

```json
{
  "methodology": {
    "tools_used": ["Navigator-recommended: ExifTool for metadata, PDF Stream Dumper for structure"]
  }
}
```

### Tool priority with Navigator
1. Navigator for tool discovery (what tool to use)
2. Configured search library for execution (using the tool)
3. Curated skill list as fallback if Navigator is down

---

## Fact-Checker Usage

Query Navigator to find verification tools appropriate to the claim type:

| Claim Type | Navigator Query | Expected Category |
|------------|----------------|-------------------|
| Image authenticity | "image forensics manipulation detection" | `image_video_analysis` |
| Corporate ownership | "company beneficial ownership verification" | `companies` |
| Domain/website claims | "domain ownership history verification" | `domains_websites` |
| Social media claims | "social media account verification" | `social_media` |
| Financial claims | "financial records public filings" | `companies` or `public_records` |
| Location claims | "geolocation verification from photo" | `geolocation_mapping` |

Use `navigator tools find --query "…" --json` for targeted tool discovery and
`navigator tools show <tool-id> --json` to preserve a selected tool's details.
Use the authenticated Navigator CLI first; the REST API is a compatibility
fallback only when the CLI is unavailable, and record that fallback in the methodology.

---

## Rate Limit Awareness

- `navigator tools find`: use for browsing and discovery
- `navigator data find` / `navigator data query`: use when structured datasets can answer a direction; preserve the source id and result artifact
- If the CLI reports an entitlement or quota limit, record the result and fall back to the curated tool list in this skill
