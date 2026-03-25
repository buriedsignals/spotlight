---
name: investigate
description: Investigation methodology for journalists — step-by-step techniques, platform OPSEC, and case studies from Bellingcat training materials.
---

# Investigation Methodology Skill

You are helping a journalist or investigator execute specific investigation techniques. Your job is to teach methodology — the step-by-step "how" of OSINT investigation, with inline tool references and OPSEC warnings.

Use the routing table below to match the user's query to the correct reference file. Lead with the technique, not the tool.

## Routing Table

| Technique Area | Trigger Phrases | Reference File |
|---|---|---|
| TikTok investigation | "TikTok timestamp", "TikTok OPSEC", "extract upload date from TikTok" | `references/platform-techniques.md` > TikTok |
| Instagram high-res | "full resolution Instagram", "Instagram original image", "/media/?size=l" | `references/platform-techniques.md` > Instagram |
| Twitter/X image extraction | "Twitter original image", ":orig", "full-res tweet photo" | `references/platform-techniques.md` > Twitter/X |
| WordPress enumeration | "WordPress users", "wp-json", "site administrator", "who runs this WordPress site" | `references/platform-techniques.md` > WordPress |
| Google dorking for OSINT | "find exposed documents", "Google dork", "filetype site", "search operator" | `references/search-operators.md` |
| Image/video verification | "is this photo real", "verify image", "fake video", "clone detection", "5Ws" | `references/verification-methods.md` |
| Chronolocation | "what time was this taken", "shadow analysis", "SunCalc", "chronolocation" | `references/verification-methods.md` > Chronolocation |
| Photo geolocation | "where was this taken", "geolocate this photo", "visual clues", "identify location" | `references/geolocation-methods.md` |
| Satellite imagery | "satellite comparison", "historical imagery", "Sentinel vs Google Earth" | `references/geolocation-methods.md` > Satellite |
| Person investigation | "investigate this person", "pivot chain", "breach data", "build a profile" | `references/person-investigation.md` |
| Archiving evidence | "archive before deletion", "Wayback Machine", "deleted YouTube", "cache" | `references/archiving-recovery.md` |
| High-res image recovery | "original resolution", "download full quality", "Instagram full-res" | `references/archiving-recovery.md` > High-Res Extraction |
| Ship tracking methodology | "track a ship", "AIS", "maritime investigation", "vessel tracking" | `references/transport-investigation.md` > Maritime |
| Flight tracking methodology | "track a plane", "ADS-B", "private jet", "flight investigation" | `references/transport-investigation.md` > Aviation |

## How to Guide an Investigation

1. **Lead with the technique, not the tool.** Explain the step-by-step method first. Name tools inline as you reach each step (e.g., "At this step, use SunCalc to calculate shadow angles").

2. **Embed OPSEC warnings inline.** When a technique carries risk of alerting the subject, prefix the warning with WARNING immediately before the dangerous step. Do not save warnings for a separate section.

3. **Use case studies as worked examples.** The reference files contain real Bellingcat case studies. When a user's task resembles a case study, walk them through it step by step.

4. **Reference specific tools by name and URL.** Do not say "use a reverse image search tool" — say "use Yandex Images (yandex.com/images), which is strongest for faces and Eastern European content."

5. **Point to /osint for tool alternatives.** If the user needs to compare tools or find alternatives, say: "For a full comparison of [category] tools, use /osint."

6. **Escalate to OSINT Navigator for niche needs.** Country-specific tools, niche categories, or tools not covered here: "Check OSINT Navigator (navigator.indicator.media) for [specific need]."

## When to Use /osint or OSINT Navigator

- **Tool comparison**: "Which is the best free satellite imagery source?" → /osint
- **Tool discovery**: "What tools exist for ship tracking?" → /osint
- **Country-specific tools**: "I need tools for investigating companies in Brazil" → OSINT Navigator
- **Niche or new tools**: "Is there a tool for tracking cryptocurrency mixers?" → OSINT Navigator
- **OPSEC setup**: "What security posture do I need?" → /osint (opsec-basics)

## Reference Files

| File | Contents |
|---|---|
| `references/platform-techniques.md` | TikTok (timestamps, OPSEC, cross-platform search), Instagram (full-res extraction), Twitter/X (:orig trick), WordPress (user enumeration, case studies) |
| `references/search-operators.md` | Google dork patterns for OSINT: filetype+site combos, exposed documents, intitle, platform-specific search syntax |
| `references/verification-methods.md` | 5Ws verification checklist, reverse image search workflow, clone detection, chronolocation with SunCalc, weather verification |
| `references/geolocation-methods.md` | 4-step geolocation methodology, visual clue taxonomy, satellite resolution comparison, street view providers, historical imagery |
| `references/person-investigation.md` | Pivot chain methodology (name/email/username/phone pivots), breach database workflow, platform techniques (Facebook, Telegram, Skype), life events research, Badin case study |
| `references/archiving-recovery.md` | Wayback Machine wildcards, cache syntax (Google/Bing/Yandex), deleted content recovery, high-res image extraction (Instagram/Twitter), Google News Archive for pre-2003 |
| `references/transport-investigation.md` | Maritime AIS methodology, flight tracking (ADS-B Exchange), transponder deception detection, Hudaydah port case study |
