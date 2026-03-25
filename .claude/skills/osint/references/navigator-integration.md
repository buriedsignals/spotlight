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

## Web Access (Free Tier)

**URL:** https://navigator.indicator.media

- 10 queries per day (resets at midnight UTC)
- Full documentation per tool (usage guides, limitations, pricing)
- No account required for browsing; email sign-in for query access
- Rich tool cards with links, categories, tags, and source attribution

---

## MCP Server (Pro Tier)

Connect to Navigator's remote MCP endpoint for live tool search directly in Claude Code or Cursor. No local install needed.

**Endpoint:** `https://navigator.indicator.media/mcp`
**Transport:** Streamable HTTP
**Auth:** OAuth 2.0 with PKCE (handled automatically by MCP clients — browser opens for email magic link)
**Requirement:** Active Indicator Media newsletter subscription (pro tier)

### Available Tools

- **`search_tools`** — Semantic search across the full tool database. Supports optional `category` filter (`image_video`, `social_media`, `geolocation`, `domain_ip`, `transportation`, `financial`, `people_search`, `archiving`, `maps`, `corporate`). Does NOT count against daily limit — use freely for discovery.
- **`query_osint`** — RAG-powered natural language answers with tool recommendations and detailed explanations. Counts against the 50/day pro limit. Best for complex questions that need synthesized answers.

### Client Setup

Add to Claude Code settings (`~/.claude/settings.json` or project `.mcp.json`):

```json
{
  "mcpServers": {
    "osint-navigator": {
      "type": "url",
      "url": "https://navigator.indicator.media/mcp"
    }
  }
}
```

First connection will open a browser for email authentication. Tokens refresh automatically for 30 days.

---

## When to Route to Navigator

This skill covers the most common tools and investigation workflows. Route to Navigator when the user needs:

- **Country-specific tools** — Navigator indexes specialized regional databases and registries not covered in this skill
- **Detailed tool documentation** — full usage guides, API details, limitations, and practical tips for a specific tool
- **Comparing alternatives** — side-by-side evaluation of multiple tools in a category with pros/cons
- **Niche categories** — areas with sparse coverage in the skill (blockchain forensics, wildlife trade tracking, conflict monitoring)
- **Recent additions** — tools added in the last few weeks from the weekly crawl cycle
- **Pricing and availability** — current free/freemium/paid status, tier details, and API access information
- **"What's new" questions** — Navigator's database reflects the latest weekly crawl

---

## Offline Fallback

This skill works standalone without Navigator access. If Navigator is unavailable or the user is offline:

1. Use the reference files in this skill for tool recommendations and investigation checklists
2. Note specific gaps where Navigator could provide deeper information (e.g., "Navigator can show you the full list of ship tracking tools with current pricing")
3. Suggest checking Navigator when connectivity is restored
