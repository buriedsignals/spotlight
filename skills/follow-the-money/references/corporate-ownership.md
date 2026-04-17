# Corporate Ownership Tracing

How to trace a company's ownership chain from the legal entity on paper to the Ultimate Beneficial Owner (UBO) -- the real person who controls it.

## When to Use This

You have a company name and need to find out who actually owns or controls it. The company may be privately held, registered in a jurisdiction with limited transparency, or owned through layers of holding companies.

## Key Concepts

### Public vs Private Companies

Public companies (listed on a stock exchange) are required to file ownership and financial information publicly. Private companies -- the kind most often involved in corruption, fraud, or organized crime -- have far fewer disclosure requirements. Most of your work will involve private companies.

### Corporate Registries vs Gazettes

Countries that collect company information typically make it available through two channels:

- **Corporate registries:** Searchable databases where you can look up company names and find basic information, sometimes including original documents, links to related companies, and ownership data.
- **Gazettes:** Official government publications that announce incorporations, changes of status, and dissolutions. These often contain ownership information at the time of incorporation.

### Access Varies by Country

Every country has different laws on company and asset filings. Many countries don't have a public repository for ownership. Some collect it but restrict access to local residents, banks, or lawyers. Some registries are regional rather than national -- you need to know which region a company is registered in.

See the Jelter company registries spreadsheet (linked from the Obsidian note) for ~100 company databases by country, including whether they're free and what data is available. For country-specific registries, check OSINT Navigator (navigator.indicator.media).

## The 6-Step Corporate Traversal Method

This method traces ownership layer by layer until you reach a natural person. The process is iterative: identify the legal owner, search that owner, repeat.

### Step 1: Primary Search

Search the company name in OpenCorporates (opencorporates.com). Note:
- Registered agent
- Officers (people and companies)
- Jurisdiction and registration number
- Company status (active, dissolved, struck off)
- Registered address

OpenCorporates covers 140+ jurisdictions and is free for journalists (register for a "Permitted User" account for full access). Use it as your primary traversal funnel, but always verify critical details against the original official registry.

### Step 2: Jurisdictional Deep Dive

Navigate to the company's official national or regional registry for full filing history. OpenCorporates links directly to source registries. This step often reveals:
- Historical ownership changes
- Previous company names
- Filed annual returns with shareholder details
- Articles of incorporation

Key registries: Companies House (UK, free, gold standard for transparency), SEC EDGAR (US public companies), Handelsregister (Germany), CNPJ (Brazil).

### Step 3: Entity Traversal

If the legal owner is another company (e.g., "Holdings Inc."), search that company in OpenCorporates. Note its jurisdiction -- if it's in a secrecy jurisdiction (BVI, Cayman Islands, Panama), see `offshore-structures.md`.

Repeat this step for each layer. Each time you find a corporate owner rather than a natural person, you need to traverse one more layer.

### Step 4: Officer Overlap

Search the names of key directors and officers in OpenCorporates' "Officers" search. This reveals all other companies and jurisdictions that share this person. This is one of the most powerful techniques:

- A director serving on dozens of unrelated companies in different jurisdictions is likely a **nominee director** -- a major red flag.
- Shared directors between two apparently unrelated companies suggest they are controlled by the same person.
- Officers who appear across multiple shell companies in secrecy jurisdictions often point to a single UBO.

### Step 5: Leak Cross-Reference

Take key company and officer names and search them in:
- **ICIJ Offshore Leaks Database** (offshoreleaks.icij.org) -- covers Panama Papers, Paradise Papers, Pandora Papers, Bahamas Leaks. Contains ~810,000 offshore entities.
- **OCCRP Aleph** (aleph.occrp.org) -- aggregates public records, leaked documents, and investigations from millions of sources. Free to browse; apply for "Friend of OCCRP" status for wider access.

Even a decade after the Panama Papers, new stories emerge from these databases. Not every connection has been reported.

### Step 6: UBO Verification

Once you've identified a candidate UBO, verify:
- **Cross-reference officer details** across independent sources: different country's registry, public voting records, LinkedIn, social media.
- **Asset/activity consistency check:** Does the UBO have ties to the target's industry or location? Inconsistencies suggest a nominee relationship.
- **Beneficial Ownership Registers:** Check the UK PSC register, EU registers, or Open Ownership (register.openownership.org) if available for the target jurisdiction.
- **The UBO threshold:** Typically 25% ownership or significant influence/control.

## Red Flags

Watch for these indicators of deliberate ownership concealment:

- **Nominee directors on 500+ entities:** A professional nominee, not a real decision-maker.
- **Shared registered addresses:** A single address (often a mailbox in a secrecy jurisdiction) used by dozens or hundreds of companies confirms a likely nominee service provider.
- **Single-address registrations:** The shell company's "office" is a residential property or post-box with no operational presence.
- **Circular ownership:** Company A owns Company B which owns Company A.
- **Recently incorporated companies winning large contracts:** Especially no-bid government contracts.
- **Directors with no digital footprint:** Real business people leave traces.

## Using OpenCorporates Effectively

Beyond basic search, OpenCorporates provides:

- **Corporate Groupings:** Related companies that OpenCorporates believes are connected, accelerating parent/subsidiary discovery.
- **Address search:** Enter a registered address to find all companies registered there.
- **Filtering:** Filter by jurisdiction, status, incorporation date to narrow results.
- **Direct registry links:** Every result links to the original government source.

## Data Preservation

Financial investigations produce chains of evidence that must be preserved:

| Step | Action | Purpose |
|---|---|---|
| Log every entity | Record: entity name, jurisdiction, registry URL, date/time collected, legal owner | Creates auditable trail connecting UBO to original target |
| Archive source pages | Save registry pages via Hunchly, Archive.today, or screenshots with timestamps | Preserves evidence of what the filing said when you collected it |
| Export visualizations | Save corporate structure diagrams as non-editable PNG/PDF | Documents your interpretation of the ownership chain |

## Credits & Attribution

Primary sources:
- Derek Bowler, "Tracing Beneficial Ownership with OSINT for Financial Crime," EBU Eurovision News Spotlight, December 2025.
- Jelter, "Follow the Money" (presentation). Company registries spreadsheet and corporate ownership methodology.
- Miranda Patrucic & Jelena Cosic, "Introduction to Investigative Journalism: Following the Money," GIJN, November 2024. Licensed under CC BY-ND 4.0.

Tool discovery: OSINT Navigator (navigator.indicator.media)
