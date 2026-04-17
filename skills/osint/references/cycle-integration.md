# Investigation Cycle Integration

How and when to use OSINT Navigator during Spotlight investigation cycles.

---

## Availability Check

Your spawn prompt includes an `INTEGRATIONS` line. If `osint_navigator=true`, you have access. If false or absent, skip Navigator calls and use the curated tool list in this skill.

```bash
# Verify access (optional — only if you need to confirm mid-execution)
curl -s -H "Authorization: Bearer $OSINT_NAV_API_KEY" \
  https://navigator.indicator.media/api/health
```

---

## PLANNING Mode (Investigator)

Query Navigator to inform your methodology. For each investigation direction:

1. Identify what type of OSINT task each step requires (corporate lookup, image verification, etc.)
2. Query Navigator for the best tools:
   ```bash
   curl -s -H "Authorization: Bearer $OSINT_NAV_API_KEY" \
     -X POST https://navigator.indicator.media/api/tools/search \
     -H "Content-Type: application/json" \
     -d '{"query": "Danish corporate registry ownership", "category": "companies"}'
   ```
3. Record selected tools in methodology.json under each direction's `steps[].tool` and `osint_techniques[]` fields
4. Use `/api/tools/search` (unlimited) for browsing categories. Reserve `/api/query` for complex questions where you need workflow advice.

---

## EXECUTION Mode (Investigator)

### At cycle start
Query Navigator for any tools needed by the approved methodology that you don't already know how to use.

### Mid-cycle (when hitting a wall)
If a planned technique fails or a new line of inquiry opens:

```bash
# Example: planned approach to verify a document failed, need alternatives
curl -s -H "Authorization: Bearer $OSINT_NAV_API_KEY" \
  -X POST https://navigator.indicator.media/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I verify the authenticity of a government PDF document?"}'
```

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

Use `/api/tools/search` with the appropriate category for targeted lookups. Use `/api/query` when the verification approach itself is unclear.

---

## Rate Limit Awareness

- `/api/tools/search`: **Unlimited** — use freely for browsing and discovery
- `/api/query`: **10/day (free) or 50/day (pro)** — use for complex, synthesized questions only
- The response includes `rate_limit.queries_remaining` — check this before making `/api/query` calls in later cycles
- If quota is exhausted, fall back to the curated tool list in this skill
