# OPSEC Basics for OSINT Research

Operational security fundamentals for journalists and researchers conducting open source investigations. Assess your threat level first, then follow the corresponding guidance.

---

## Threat Level Assessment

Before starting any investigation, assess your threat level. This determines the precautions you need. When in doubt, treat the threat level as one tier higher than you think.

### Low — Public Records Research

Investigating publicly available data: company registries, court records, published news, open datasets. No expectation that the subject monitors who accesses the data.

- Standard browser is acceptable
- Be aware that your IP is logged by every site you visit
- Use a separate browser profile for research (isolates cookies, history, and saved logins from personal browsing)
- Avoid logging in to personal accounts in the same browser session
- Consider using a bookmark-free, extension-free profile dedicated to research

### Medium — Active Investigation of Individuals or Companies

Investigating private companies, politically connected individuals, or organizations that may actively monitor interest in them. This is the most common tier for journalism.

- **VPN** (reputable, no-logs provider) for all research activity. Ensure DNS leak protection is enabled.
- **Separate browser profile** with no personal extensions, saved logins, or autofill data. Firefox with a clean profile is a good default.
- **Research-only accounts** for any platform signups. Use a dedicated email address (ProtonMail or similar) that is not linked to your identity. Never use personal email.
- **Be aware that lookups are logged** — WHOIS queries record your IP, social media profile visits trigger notifications (LinkedIn, some Instagram analytics), some public records services log and sell access data.
- **Separate device** or virtual machine if possible. A cheap Chromebook or a VM running Linux works well.
- **No direct contact** with investigation subjects from identifiable accounts. Any outreach should come from a clearly journalistic identity, not a personal or sock-puppet account.
- **Metadata hygiene** — strip metadata from any documents or images before sharing outside your team. Your device name, OS username, and GPS coordinates may be embedded.

### High — Hostile Actors, Organized Crime, State-Level Targets

Investigating state actors, intelligence services, organized crime, or anyone with the capability and motivation to identify and retaliate against researchers. This tier requires organizational support.

- **Tor Browser or Tails OS** for all research. Tails leaves no trace on the host machine and routes all traffic through Tor.
- **Disposable accounts** created over Tor with no link to your identity. Use temporary email services for signup. Rotate accounts between sessions.
- **Air-gapped analysis** — download data onto a USB drive, transfer to an air-gapped machine (never connected to the internet), and analyze offline.
- **Assume all activity is monitored** — network traffic, device fingerprinting, timing analysis, and physical surveillance. State actors can correlate Tor usage timing with other network observations.
- **Physical security** — consider who has access to your device and workspace. Use full-disk encryption. Power off devices when not in use (not just sleep).
- **Compartmentalize** — do not discuss investigation details on the same device or network used for research. Use encrypted communication (Signal) for team coordination.
- **Consult your organization's security team** before beginning. Have a response plan for if your identity is discovered.

---

## Universal Rules

These apply at every threat level:

- **Never access systems without authorization.** OSINT means open source. If you need a password or have to bypass access controls, it is not open source intelligence.
- **Archive before engaging.** Save pages, screenshots, and metadata before any action that might alert the target (viewing a profile, submitting a query, making a phone call). Targets may delete content once aware of scrutiny.
- **Do not use your real identity** on investigation targets. No personal accounts, no real name, no identifiable behavioral patterns.
- **Use a research-only device** when possible, or at minimum a dedicated browser profile with VPN.
- **Document your process.** Record what you accessed, when, how, and from which account/IP. This protects both the investigation's integrity and your legal standing.
- **Know your legal boundaries.** Data protection laws (GDPR, CCPA), computer fraud and access laws (CFAA in US), and platform terms of service vary by jurisdiction. Get legal review before scraping at scale, using breach data, or conducting pretextual outreach.
- **Preserve the chain of evidence.** If findings may be used in legal proceedings, maintain verifiable archives with timestamps (Hunchly, Auto Archiver) and avoid altering original content.

---

## Common Mistakes

- **Using personal email for tool signups** — links your real identity to the investigation and to any breach of that tool's database
- **Forgetting that WHOIS lookups are logged** — the domain owner may see your query IP in registrar access logs. Some registrars offer "stealth" WHOIS, but most do not.
- **Not using VPN when accessing target infrastructure** — your IP appears in server logs, web analytics dashboards, CDN records, and potentially the target's security monitoring
- **Social engineering without legal review** — pretexting and impersonation carry criminal liability in many jurisdictions, even for journalists
- **Reusing research accounts across investigations** — creates a behavioral pattern that links separate investigations and can expose your identity
- **Checking investigation targets from personal social media** — LinkedIn shows profile visitors by default, Instagram business accounts see viewer data, and Facebook can surface your profile in the target's "People You May Know"
- **Neglecting browser fingerprinting** — even with a VPN, your browser configuration (installed fonts, screen resolution, timezone, extensions, canvas rendering) creates a nearly unique fingerprint. Use Tor Browser or a clean Firefox profile with resistFingerprinting enabled.
- **Failing to check your own exposure** — before beginning an investigation, search for yourself using the same tools you plan to use on the target. Know what your digital footprint looks like.
