# OSINT Navigator Integration

How to use OSINT Navigator alongside this skill for deeper tool discovery and detailed documentation.

---

## What Navigator Offers

OSINT Navigator is a RAG-powered search engine for OSINT tools, maintained by Indicator Media.

- **1,000+ tools** with detailed documentation, pricing info, and category tags
- **Semantic search** — ask natural language questions, get tool recommendations with usage explanations
- **Community-cached answers** — popular queries return vetted, voted-on responses for fast results
- **Weekly updates** from 9 curated sources (Bellingcat, Awesome OSINT, Digital Digging, OSINT Vault, PikaOSINT, and others)
- **Tool documentation** — many tools have multi-paragraph descriptions including capabilities, limitations, pricing tiers, and practical tips

---

## REST API Access

**Base URL:** `https://navigator.indicator.media`
**Auth:** Bearer token via env var `$OSINT_NAV_API_KEY` (format: `on_xxxxx`)
**Rate limits:** `/api/query` = 10/day free, 50/day pro. `/api/tools/search` = unlimited.

### Two Endpoints

**1. `/api/query` — Natural language tool recommendations (rate-limited)**

Use for complex questions that need synthesized answers: "How do I verify company ownership in Denmark?"

```bash
curl -s -H "Authorization: Bearer $OSINT_NAV_API_KEY" \
  -X POST https://navigator.indicator.media/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I verify company ownership in Denmark?"}'
```

Response:
```json
{
  "answer": "Markdown text with tool recommendations and explanations...",
  "tools": [
    {
      "tool_id": "opencorporates-a1b2c3d4",
      "tool_name": "OpenCorporates",
      "tool_url": "https://opencorporates.com",
      "category": "companies",
      "description": "Largest open database of company information..."
    }
  ],
  "cache_hit": true,
  "rate_limit": {
    "queries_used": 5,
    "queries_remaining": 45,
    "limit": 50
  }
}
```

**2. `/api/tools/search` — Direct database search (unlimited)**

Use for browsing by keyword or category. Does NOT consume daily quota.

```bash
curl -s -H "Authorization: Bearer $OSINT_NAV_API_KEY" \
  -X POST https://navigator.indicator.media/api/tools/search \
  -H "Content-Type: application/json" \
  -d '{"query": "corporate registry", "category": "companies"}'
```

Response:
```json
[
  {
    "tool_id": "opencorporates-a1b2c3d4",
    "tool_name": "OpenCorporates",
    "tool_url": "https://opencorporates.com",
    "category": "companies",
    "description": "Largest open database of company information...",
    "tags": ["corporate", "registry", "ownership"]
  }
]
```

### When to Use Which

| Situation | Endpoint | Why |
|-----------|----------|-----|
| "How do I investigate X?" | `/api/query` | Need synthesized answer with workflow |
| "What tools exist for X?" | `/api/tools/search` | Browsing/discovery, save your quota |
| Comparing tools in a category | `/api/tools/search` | Unlimited, returns multiple results |
| Stuck mid-investigation | `/api/query` | Need contextual advice, not just a list |

### 20 Tool Categories

`search`, `people`, `social_media`, `usernames_accounts`, `emails`, `phone_numbers`, `domains_websites`, `ip_address_network`, `geolocation_mapping`, `image_video_analysis`, `companies`, `public_records`, `transport`, `monitoring`, `web_archiving`, `documents_code`, `dark_web_data_breaches`, `cryptocurrency`, `data_analysis_visualization`, `ai`

---

## When to Route to Navigator

This skill covers the most common tools and investigation workflows. Route to Navigator when:

- **Country-specific tools** — Navigator indexes specialized regional databases and registries
- **Detailed tool documentation** — full usage guides, API details, limitations, and practical tips
- **Comparing alternatives** — side-by-side evaluation of tools with pros/cons
- **Niche categories** — areas with sparse coverage in the skill (blockchain forensics, wildlife trade)
- **Recent additions** — tools added in the last few weeks from the weekly crawl cycle
- **Pricing and availability** — current free/freemium/paid status and tier details

---

## Offline Fallback

This skill works standalone without Navigator access. If Navigator is unavailable or `$OSINT_NAV_API_KEY` is not set:

1. Use the reference files in this skill for tool recommendations and investigation checklists
2. Note specific gaps where Navigator could provide deeper information
3. Suggest checking Navigator when connectivity is restored
