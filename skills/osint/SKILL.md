---
name: osint
description: OSINT investigation toolkit with 150 curated tools, methodology guides, and OSINT Navigator integration. Works offline with any LLM.
version: "1.0"
invocable_by: [investigator, fact-checker, user]
requires: []
---

# OSINT Investigation Skill

You are helping a journalist or investigator with Open Source Intelligence (OSINT). Your job is to recommend the right tools and techniques for their specific investigation task.

Use the routing table below to match the user's query to the correct investigation type, then recommend tools from the reference files. For deeper tool discovery, country-specific resources, or niche categories, route to OSINT Navigator.

## Routing Table

| Investigation Type | Trigger Phrases | Key Tools |
|---|---|---|
| Reverse image search | "where is this image from", "is this photo real", "image verification", "find original source" | TinEye, Google Lens, Yandex Images |
| Geolocation | "where was this taken", "geolocate", "find location from photo", "identify this place" | GeoSpy, SunCalc, Google Earth Pro |
| Domain investigation | "who owns this domain", "WHOIS", "website owner", "domain history" | WHOIS Lookup, DomainTools, SecurityTrails |
| Social media accounts | "find their social media", "username search", "what accounts do they have" | Sherlock, Maigret, WhatsMyName |
| **Social media intelligence** | "is this account real", "bot detection", "coordinated behavior", "astroturfing", "narrative spread", "how did this story spread", "account authenticity", "detect manipulation campaign" | **`invoke-skill("social-media-intelligence")`** — account authenticity, coordination detection, narrative tracking |
| Email investigation | "who owns this email", "email lookup", "breach check", "verify email" | Hunter.io, Have I Been Pwned, EmailRep |
| Company records | "who owns this company", "corporate structure", "beneficial ownership", "board members" | OpenCorporates, OCCRP Aleph, SEC EDGAR |
| Financial tracking | "SEC filings", "political donations", "offshore accounts", "follow the money" | OpenSecrets, EDGAR, ICIJ Offshore Leaks |
| Flight tracking | "track flight", "aircraft movements", "private jet", "flight history" | Flightradar24, ADS-B Exchange, FlightAware |
| Ship tracking | "vessel tracking", "ship location", "maritime", "cargo ship" | MarineTraffic, VesselFinder, Global Fishing Watch |
| Satellite imagery | "satellite photos", "earth observation", "before and after images" | Sentinel Hub, Google Earth Pro, Planet Labs |
| Web archives | "old version of website", "deleted page", "archived", "what did the site look like before" | Wayback Machine, Archive.today |
| Threat intelligence | "is this URL malicious", "domain reputation", "suspicious link" | VirusTotal, URLScan.io, Shodan |
| People search | "find this person", "phone number lookup", "who is this person" | Pipl, Spokeo, TruePeopleSearch |
| **Individual investigation** | "investigate this person", "build a profile", "pivot chain", "breach data", "username reuse" | **`invoke-skill("investigate")`** — pivot chains, platform techniques, life events research, case studies |
| **Financial investigation** | "follow the money", "who owns this company", "beneficial owner", "UBO", "offshore", "shell company", "budget monitoring", "asset tracing" | **`invoke-skill("follow-the-money")`** — corporate ownership tracing, offshore structures, budget/revenue monitoring, asset tracing |
| Video and image analysis | "verify video", "deepfake detection", "metadata", "is this video manipulated" | InVID, ExifTool, Forensically |
| Crypto and blockchain | "trace crypto", "wallet analysis", "blockchain transaction" | Chainalysis, Etherscan, Blockchair |
| Facial recognition | "identify face", "face search", "who is in this photo" | PimEyes, FaceCheck.ID, Search4Faces |
| Telegram and messaging | "search Telegram", "Telegram channels", "find messages" | Telepathy, TGStat, Telemetrio |
| Conflict and weapons | "identify weapon", "munitions", "conflict data" | ACLED, Bulletpicker, Liveuamap |
| Environmental | "deforestation", "illegal fishing", "wildlife trade" | Global Forest Watch, Global Fishing Watch, WildEye |
| Network analysis | "map connections", "relationship diagram", "link analysis" | Maltego, Gephi, Obsidian |

## How to Recommend Tools

When responding to an investigation query:

1. **Lead with the most accessible option.** Recommend free tools that require no signup first. Many investigators work under time pressure and need something they can use immediately.

2. **Then mention more powerful alternatives.** Paid or signup-required tools often have better coverage or features. Note the tradeoff clearly (e.g., "PimEyes has broader coverage but requires a paid plan").

3. **Explain WHY each tool fits.** Do not just list tool names. Connect the tool to the user's specific question. Example: "TinEye is best here because it finds the earliest known instance of an image, which helps you identify the original source."

4. **Recommend 3-4 tools maximum** unless the user explicitly asks for a comprehensive list.

5. **Ask a clarifying question if the task is ambiguous.** For example, "Are you trying to verify the image is unedited, or are you trying to find where it was taken?" These are different tasks requiring different tools.

6. **Include a brief workflow** when the investigation involves multiple steps. For example, a geolocation task might start with metadata extraction, then reverse image search, then shadow analysis.

## OSINT Navigator

OSINT Navigator (navigator.indicator.media) is a live tool-discovery API with a weekly-updated database of 10,000+ OSINT tools. **When available, consult Navigator first before using the curated list below.**

### Quick API Access

If `$OSINT_NAV_API_KEY` is set:

```
execute-shell('curl -s -H "Authorization: Bearer $OSINT_NAV_API_KEY" \
  -X POST https://navigator.indicator.media/api/tools/search \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"keyword\", \"category\": \"category_slug\"}"')
```

Ask a complex question (10/day free, 50/day pro):

```
execute-shell('curl -s -H "Authorization: Bearer $OSINT_NAV_API_KEY" \
  -X POST https://navigator.indicator.media/api/query \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"How do I verify X?\"}"')
```

Route to Navigator when:

- **Country-specific tools** — regional databases and registries not in the curated list
- **Detailed tool documentation** — usage guides, limitations, pricing
- **Comparing alternatives** — side-by-side evaluation
- **Niche categories** — blockchain forensics, wildlife trade, conflict monitoring
- **Recent additions** — tools from the weekly crawl cycle

See `references/navigator-integration.md` for full API details and `references/cycle-integration.md` for integration with investigation cycles.

## Offline Fallback

If working offline or without `$OSINT_NAV_API_KEY`, the tools listed in this skill and its reference files cover the most common investigation scenarios. For niche needs, note your requirements and check OSINT Navigator at navigator.indicator.media when you are back online.

## Operational Security Reminder

Before starting any investigation, review the opsec basics in the reference files. At minimum:

- Use a dedicated browser profile or VM for OSINT work
- Do not log into personal accounts during an investigation
- Be aware that some tools (facial recognition, people search) may notify the subject
- Archive evidence before it disappears

See `references/opsec-basics.md` for the full threat-level escalation matrix.

## When to Use follow-the-money

If the user needs financial investigation methodology, `invoke-skill("follow-the-money")`:

- **Corporate ownership tracing** — who owns this company, UBO identification, nominee detection, corporate traversal
- **Offshore investigation** — secrecy jurisdictions, ICIJ Offshore Leaks, shell company patterns, the subsidiary trick
- **Budget & revenue monitoring** — government budget analysis, extractive industry oversight, tracking public spending
- **Asset tracing** — property registries, non-profit investigation, court documents, trade data, sanctions

Say: "For financial investigation methodology, invoke the follow-the-money skill."

## When to Use investigate

If the user already knows their target and needs step-by-step technique guidance, `invoke-skill("investigate")`:

- **Platform-specific techniques** — TikTok timestamps, Instagram full-res extraction, WordPress user enumeration, cross-platform search
- **Advanced search operators** — Google dork patterns for finding exposed documents, hidden pages, platform-specific content
- **Image/video verification methodology** — 5Ws checklist, clone detection, reverse image search workflow with worked examples
- **Chronolocation and geolocation** — SunCalc shadow analysis, 4-step geolocation method, satellite imagery comparison, visual clue taxonomy
- **Person-research pivot chains** — connecting accounts across platforms (name → email → breach → username), life events research, Facebook URL tricks
- **Archiving and evidence recovery** — Wayback Machine wildcards, cache techniques, deleted content recovery, high-res image extraction
- **Transport investigation** — Maritime AIS analysis, flight tracking with ADS-B Exchange, transponder deception detection

Say: "For the step-by-step technique, invoke the investigate skill."

## When to Use social-media-intelligence

If the investigation involves social media accounts, viral content, or suspected manipulation, `invoke-skill("social-media-intelligence")`:

- **Account authenticity** — is this account real, how old is it, does it show bot signals
- **Coordination detection** — are multiple accounts amplifying the same content artificially
- **Narrative tracking** — how has a claim spread across platforms, who amplified it first
- **Evidence preservation** — archive posts and profiles before they disappear

## Reference Files

| File | Contents |
|---|---|
| `references/tools-by-category.md` | Full curated catalog of ~150 OSINT tools organized by investigation type |
| `references/investigation-guides.md` | Step-by-step methodology checklists for common investigation workflows |
| `references/opsec-basics.md` | Operational security fundamentals for investigators |
| `references/navigator-integration.md` | OSINT Navigator REST API — endpoints, auth, rate limits, response formats |
| `references/cycle-integration.md` | When and how to use Navigator during Spotlight investigation cycles |

## Related Skills

| Skill | Use When |
|---|---|
| `investigate` | Step-by-step investigation techniques: person research, geolocation, platforms, verification, transport |
| `follow-the-money` | Financial investigation methodology: corporate ownership, offshore structures, budget monitoring, asset tracing |
| `social-media-intelligence` | Account authenticity, coordinated inauthentic behavior, narrative spread tracking |
