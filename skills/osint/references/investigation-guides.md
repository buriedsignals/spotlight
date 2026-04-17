# Investigation Guides

Quick-reference checklists for common OSINT investigation types. Each step names the tool or tool category to use. Steps are ordered — follow them sequentially for best results.

For step-by-step methodology with case studies and worked examples, use `/investigate`.

| Investigation Type | /investigate Reference |
|---|---|
| Image/video verification | verification-methods.md |
| Geolocation from photos | geolocation-methods.md |
| People search & profiling | person-investigation.md |
| Transport tracking | transport-investigation.md |
| Social media platforms | platform-techniques.md |
| Finding hidden documents | search-operators.md |
| Archiving & recovery | archiving-recovery.md |

---

## Image/Video Verification

Goal: Determine whether an image or video is authentic and represents what it claims.

1. **Extract metadata** — ExifTool or xIFr for EXIF data: timestamps, camera model, GPS coordinates, software used for editing. Note: social media platforms strip most metadata on upload.
2. **Reverse image search** — TinEye (finds exact matches and earliest known instance online), Google Lens (visual similarity, finds copies and context), Yandex Images (particularly strong on faces and Eastern European content).
3. **Check for manipulation** — Forensically (Error Level Analysis reveals spliced regions, clone detection finds copied areas), InVID WeVerify plugin (video keyframe extraction, magnifier, forensic filters), FotoForensics (JPEG quality analysis).
4. **Detect AI-generated content** — Sensity.ai (multilayer analysis: pixel forensics, voice analysis, file metadata; court-ready reports), DeepWare (video deepfake detection), AI Detector (AI-generated text and images).
5. **Verify context** — Cross-check the claimed date/location against: metadata timestamps, historical weather data for that location, sun position and shadows (SunCalc), known events on that date, satellite imagery from that period.
6. **Archive evidence** — Archive.today for instant snapshots, Wayback Machine for long-term preservation. Do this early — originals may be deleted once an investigation becomes known.

For the full 5Ws checklist, chronolocation methodology, and case studies: `/investigate` → verification-methods.md

## Geolocation from Media

Goal: Determine where a photo or video was taken using visual and technical evidence.

1. **Check EXIF GPS** — Run ExifTool first. Most social platforms strip GPS on upload, but messaging apps (WhatsApp, Signal), forums, and direct shares often preserve it.
2. **Analyze visual clues** — Systematically catalog: language on signs and storefronts, license plate formats, vegetation and terrain type, road markings and driving side, architecture style, power line and utility pole design, sun position.
3. **AI geolocation** — Upload to GeoSpy (most accurate, meter-level for law enforcement use), Picarta (strong on aerial imagery), GeoFinder (analyzes environmental and architectural clues). Use multiple tools and compare predictions.
4. **Shadow and sun analysis** — SunCalc calculates sun position for any date/time/location. Match shadow direction and length to estimate time of day, season, and hemisphere. Shadow Finder can narrow location candidates.
5. **Cross-reference with street view** — Google Street View, Mapillary (crowdsourced, often has coverage Google misses), KartaView. Match the camera perspective, compare building facades, signage, and road features.
6. **Confirm with satellite imagery** — Google Earth Pro (historical imagery slider for temporal matching), Sentinel Hub (free Copernicus data, recent), Planet Labs (daily captures, highest temporal resolution).
7. **Triangulate landmarks** — PeakVisor (match mountain silhouettes against global terrain data), GeoHints (reference database of country-specific visual indicators like bollards, road signs, phone numbers).

For the full 4-step methodology, visual clue taxonomy, satellite comparison, and case studies: `/investigate` → geolocation-methods.md

## Domain Investigation

Goal: Uncover who owns a domain, what infrastructure it uses, and what other domains are connected to it.

1. **WHOIS lookup** — Check current registrant, registrar, creation and expiry dates. Note if privacy protection (WhoisGuard, Domains By Proxy) is active — this is itself a data point.
2. **DNS records** — Query A, MX, CNAME, NS, TXT records. MX records reveal email provider, A records show hosting, TXT records may contain SPF/DKIM/verification tokens that link to other services.
3. **Historical WHOIS** — DomainTools or WhoisXML API for past registration data. Privacy protection is often added later — earlier records may show the real registrant.
4. **Certificate transparency** — Search crt.sh for all TLS certificates issued for the domain. Reveals subdomains, wildcard patterns, and related domains sharing the same certificate.
5. **Website technology** — BuiltWith or Wappalyzer to identify CMS, analytics IDs, ad network IDs, payment processors. Shared Google Analytics or AdSense IDs link domains to the same operator.
6. **Web archives** — Wayback Machine for historical content snapshots. Look for past contact pages, about pages, privacy policies, and DNS/hosting changes over time.
7. **Related domains** — Reverse IP lookup (who else is on this server), shared analytics ID search, favicon hash search via Shodan, reverse Google Analytics lookup. These techniques reveal networks of related sites.

For WordPress-specific enumeration techniques and case studies: `/investigate` → platform-techniques.md > WordPress

## Corporate Investigation

Goal: Map a company's structure, ownership, officers, and financial connections.

1. **Company registry** — OpenCorporates (140+ jurisdictions, free search), national registries: Companies House (UK), SEC EDGAR (US), Handelsregister (Germany), CNPJ (Brazil). Get registration number, status, registered address, incorporation date.
2. **Directors and officers** — Extract current and historical directors from registry filings. Cross-reference names across jurisdictions to find individuals serving on multiple boards.
3. **Beneficial ownership** — Open Ownership register, national UBO registers (mandatory in EU since 2020), ICIJ Offshore Leaks Database. Look for nominee directors and shell company structures.
4. **Sanctions and watchlists** — OpenSanctions (aggregated global data), EU Sanctions Map, OFAC SDN list (US), UN consolidated list. Check company name, aliases, directors, and associated entities.
5. **Financial filings** — SEC EDGAR for 10-K, 10-Q, proxy statements (US), Companies House annual accounts (UK), national gazette publications. Look for related-party transactions, subsidiary lists, major shareholders.
6. **Corporate network mapping** — Map parent companies, subsidiaries, and joint ventures. Identify shared directors, shared registered agents, and common addresses. Visualize with Maltego or Gephi.

## Social Media Investigation

Goal: Build a comprehensive picture of a person or organization's online presence and network.

1. **Username search** — Sherlock, Maigret, or Blackbird to enumerate accounts across 400+ platforms from a known username. WhatsMyName for additional coverage.
2. **Profile archiving** — Archive.today or Auto Archiver immediately. Profiles and posts may be deleted once the subject becomes aware of scrutiny. Screenshot key content with timestamps.
3. **Content analysis** — Examine posts, comments, likes, shares, and follows for connections, interests, locations, travel patterns, and associates. Note language, timezone clues, and posting patterns.
4. **Geo-tagged content** — Instagram location search for posts at specific places, MW Geofind for YouTube video locations, Twitter/X advanced search with geocode operator, Snapchat Map for public stories.
5. **Network mapping** — Analyze followers/following overlap, group memberships, tagged users, co-appearances in photos. Build a social graph and visualize with Gephi or Maltego to identify clusters and key connections.
6. **Timeline reconstruction** — Build a chronological history from all discovered accounts. Establish patterns of life: work hours, travel dates, relationships, affiliations, and changes over time.

For platform-specific techniques (TikTok timestamps, Instagram full-res, WordPress enumeration): `/investigate` → platform-techniques.md

## People Search

Goal: Identify and build a profile on a person of interest from limited starting information.

For detailed pivot chain methodology, platform-specific techniques, life events research, and case studies: `/investigate` → person-investigation.md

1. **Username enumeration** — Start with any known username. Sherlock and Maigret search 400+ sites. Also check namechk.com and checkusernames.com.
2. **Email investigation** — GHunt (Google account data), Have I Been Pwned (breach exposure), email permutation tools. Facebook Page Roles method confirms email-to-account connection (see /investigate).
3. **Breach data** — Have I Been Pwned (free), DeHashed, Intelligence X (intelx.io). Note: legal and ethical considerations apply.
4. **Social media profiles** — Cross-reference usernames and emails. Check LinkedIn, Facebook, Instagram, Twitter/X, Reddit, GitHub, VK, Odnoklassniki. Facebook default URL trick: facebook.com/first.last1 (see /investigate).
5. **Public records** — Court records (PACER in US), voter registrations, property records, business registrations. Country-specific — see Navigator.
6. **Phone number investigation** — Telegram/Skype contact lookup, TrueCaller, regional contact book apps. See /investigate for step-by-step platform techniques.
7. **Life events research** — Obituaries (most fruitful), wedding announcements, birth announcements. See /investigate for techniques and case studies.

## Financial Tracking

Goal: Follow money flows through corporate structures, political donations, and offshore entities.

For full financial investigation methodology -- including step-by-step corporate ownership tracing, offshore structure investigation, budget/revenue monitoring, and asset tracing -- use `/follow-the-money`.

Quick checklist for common starting points:

1. **Public filings** — SEC EDGAR for US public companies (10-K, 10-Q, 8-K, proxy statements), Companies House (UK annual accounts), national business registries for financial statements and annual reports.
2. **Political donations** — OpenSecrets/FEC (US federal), state election commission databases, Electoral Commission (UK), national disclosure databases. Track donors, recipients, PACs, and bundlers.
3. **Offshore structures** — ICIJ Offshore Leaks Database covers Panama Papers, Paradise Papers, Pandora Papers, and Bahamas Leaks. Search by name, jurisdiction, intermediary, or address. See `/follow-the-money` for detailed methodology.
4. **Sanctions screening** — OpenSanctions (aggregated), OFAC SDN list, EU consolidated list, UN Security Council list. Check all name variations, associated companies, and known aliases.
5. **Beneficial ownership** — National UBO registers (EU members, UK), Open Ownership global register, corporate tree analysis to identify ultimate controllers behind nominee and shell structures. See `/follow-the-money` for the 6-step corporate traversal method.
6. **Development finance** — IDI Follow The Money toolkit for DFI-funded projects, World Bank and IFC project disclosure portals, Asian Development Bank project database. Useful for investigating infrastructure and extractive industry projects.

## Transport Tracking

Goal: Track the movements of aircraft, ships, or vehicles.

1. **Aircraft** — Flightradar24 (live + 365 days history), FlightAware (strong US coverage), ADS-B Exchange (unfiltered feed, no military/government blocks, community-run).
2. **Ships** — MarineTraffic (live AIS tracking, vessel details, port calls), VesselFinder (similar AIS coverage, free tier), Global Fishing Watch (fishing vessels, "dark" vessels).
3. **Vehicles** — License plate format databases (country-specific), national vehicle registries, ANPR camera data. See Navigator for country-specific tools.
4. **Historical routes** — ADS-B Exchange for aircraft, MarineTraffic for vessels, satellite imagery time series for ground vehicles.
5. **Anomaly detection** — Transponder gaps, unusual route deviations, ship-to-ship transfers, flights to unusual airports, sanctions evasion patterns.

For maritime AIS methodology, transponder deception detection, and the Hudaydah case study: `/investigate` → transport-investigation.md
