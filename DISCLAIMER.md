# Disclaimer

Please read this before using Spotlight for any publication-bound work.

## Editorial responsibility stays with you

Spotlight is an investigation assistant. It produces findings, fact-check verdicts, and evidence archives — but **the editorial judgment is yours**. Nothing Spotlight outputs is fit to publish without a journalist reviewing, verifying, and taking responsibility for the claims.

Specifically:

- Agent outputs can be wrong. LLMs hallucinate, miss context, conflate entities, and mis-quote sources. Spotlight's evidence grounding rules reduce this (every finding must have a scraped `local_file` and archive URL) but they don't eliminate it.
- Fact-check verdicts are AI-generated, not a professional fact-checking service. Treat `verified` as "our automated pass found 2+ independent sources", not as "this is true".
- Confidence levels reflect the tool's internal assessment. A `high confidence` finding can still be wrong. Open every source; read the primary document.

**If you publish something sourced through Spotlight, you — not Buried Signals, not the model provider, not the upstream data sources — are responsible for its accuracy.**

## OPSEC is your responsibility

Spotlight's skills include OPSEC guidance (`skills/osint/references/opsec-basics.md`), and the sensitive mode strips network verbs. But:

- The tool does not enforce your threat model. If you're investigating a subject who could retaliate, you must apply appropriate operational security (VPN, isolated device, source protection).
- API calls to firecrawl, OSINT Navigator, and other integrations go to third-party services. Read their privacy policies. Assume logs exist.
- Sensitive mode does not protect against on-device compromise. If your laptop is compromised, your investigation files are too.
- Spotlight writes to local files only by default. Do not put raw source material or working hypotheses in your vault if you wouldn't want them on disk.

For anything where the threat model matters (sources at risk of retaliation, state-adversary investigations, leaked document handling), consult your organization's security team — not this tool.

## Third-party services

Spotlight calls out to third-party services when configured. Each has its own terms and privacy policy. You are responsible for compliance:

- **Firecrawl** — web scraping API. Subject to source site ToS and Firecrawl's terms.
- **OSINT Navigator** — tool discovery API. Subject to Indicator Media terms.
- **LLM providers** (Anthropic, OpenAI, Google, Fireworks, OpenRouter, Together) — queries and some context are sent to the chosen provider. Review their privacy policies. For maximum privacy, use Local mode (Ollama + fine-tuned model on your machine).
- **ACLED, Junkipedia, browser-use** — subject to each service's terms.
- **Obsidian** — your vault is local unless you enable Obsidian's sync service.

## Data you provide

- API keys you enter in `setup.html` are written only to your local `.env` file (chmod 600). They never leave your machine.
- Investigation case files are stored locally in `cases/` (or wherever you set `cases_root`). Nothing is uploaded to Buried Signals.
- Your vault stays on your machine. Buried Signals has no access to your findings.

## Scraping, copyright, and fair use

Spotlight scrapes web content for investigation purposes. You are responsible for complying with copyright and fair-use provisions in your jurisdiction when quoting or publishing findings. The tool archives source material for editorial accountability and legal defensibility — not for redistribution.

## Not legal advice

This document is for informational purposes. It is not legal advice. If your work involves regulated data (health records, minors, GDPR/CCPA-protected personal data, classified or embargoed information), consult a lawyer before using any AI-assisted investigation tool.

## No warranty

Spotlight is provided "as is" per the MIT License. Buried Signals makes no warranty about its accuracy, completeness, or fitness for any particular investigation. If the tool produces wrong, harmful, or libelous output, the liability is yours as the publisher — not Buried Signals'.

## Reporting issues

If you find a bug, a factual error baked into the methodology, or a privacy/security concern:

- GitHub Issues: https://github.com/buriedsignals/spotlight/issues
- Email: tom@buriedsignals.com

For security issues (credential leaks, API key exposure patterns, etc.), email directly before filing a public issue.

---

**By installing Spotlight, you acknowledge that you have read this disclaimer and accept responsibility for editorial oversight of any work produced with the tool.**
