# OSINT Navigator Integration

How to use OSINT Navigator alongside this skill, through the `navigator` CLI, for
deeper tool discovery and detailed documentation.

---

## What Navigator Offers

OSINT Navigator is a RAG-powered search engine for OSINT tools, maintained by
Indicator Media.

- **1,000+ tools** with detailed documentation, pricing info, and category tags
- **Semantic search** — describe what you need in plain words, get ranked matches
- **Weekly updates** from curated sources (Bellingcat, Awesome OSINT, Digital
  Digging, OSINT Vault, PikaOSINT, and others)
- **Tool documentation** — many tools have multi-paragraph descriptions covering
  capabilities, limitations, pricing tiers, and practical tips

---

## The `navigator tools` CLI

Discovery runs through the CLI — not curl, not an MCP. Call it directly:

```bash
navigator tools find "<what you need, in plain words>" --json
navigator tools find "company registry norway" --category public_records --limit 10 --json
navigator tools show "<tool_id>"
```

`find` returns semantic matches; `show` returns one tool's full record + usage docs.

**`find --json` shape:**
```json
{
  "tools": [
    {
      "tool_id": "proff-41f6c311",
      "tool_name": "Proff",
      "tool_url": "http://www.proff.no/",
      "short_description": "Detailed company information for Norway...",
      "category": "public_records",
      "tags": ["government_records"]
    }
  ]
}
```

### Auth — one-time

`navigator tools` needs a membership PAT (Tools mode is a pro-tier feature):

- **Engine-managed installs:** run `bsig auth login` during configuration.
  Engine keeps the PAT in its credential store and injects it only into a
  Mycroft or Spotlight child process that claims the Navigator skill.
- **Standalone compatibility:** run `navigator auth login` once. A magic link stores the PAT
  in the OS keychain; every later `find`/`show` reuses it — no re-auth.
- **Non-interactive (Docker/CI/headless):** set `NAVIGATOR_PAT` (or reuse
  `$OSINT_NAV_API_KEY`, format `on_xxxxx`) in the environment — the CLI uses it
  instead of the browser flow.

If the CLI reports `Not logged in` or `Session expired`, surface that and stop —
do not fall back to raw curl or a different endpoint.

### 20 Tool Categories

`search`, `people`, `social_media`, `usernames_accounts`, `emails`,
`phone_numbers`, `domains_websites`, `ip_address_network`, `geolocation_mapping`,
`image_video_analysis`, `companies`, `public_records`, `transport`, `monitoring`,
`web_archiving`, `documents_code`, `dark_web_data_breaches`, `cryptocurrency`,
`data_analysis_visualization`, `ai`

---

## When to Route to Navigator

This skill covers the most common tools and workflows. Use `navigator tools find`
when you need:

- **Country-specific tools** — specialized regional databases and registries
- **Detailed tool documentation** — full usage guides, limitations, tips (`show`)
- **Comparing alternatives** — multiple ranked matches for a category
- **Niche categories** — sparse coverage in the skill (blockchain forensics,
  wildlife trade)
- **Recent additions** — tools added in the last few weeks from the crawl cycle
- **Pricing and availability** — current free/freemium/paid status and tier details

---

## Offline Fallback

This skill works standalone without Navigator. If `navigator` is unavailable, not
installed, or not authenticated:

1. Use the reference files in this skill for tool recommendations and checklists
2. Note specific gaps where Navigator could provide deeper information
3. Run `navigator tools find` once connectivity + login are restored
