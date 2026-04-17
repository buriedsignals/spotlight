# Asset Tracing

How to find and trace assets -- property, non-profit finances, court records, trade data, art, and other vehicles used to hide or launder wealth.

## When to Use This

You've identified a person or company through corporate ownership tracing (see `corporate-ownership.md`) and need to find their assets. Or you're investigating how money is being moved through non-financial vehicles like property, art, trade, or charitable organizations.

## Property and Land Registries

Very few countries have online resources showing property ownership. When information is available, it's usually through regional (not national) websites with restricted access.

### Access Patterns by Country

- **US:** One of the few countries where you can search by a person's name or company name in many states. County assessor and recorder websites often provide free access to deeds, mortgages, and ownership records.
- **UK:** HM Land Registry allows searches by address (not name). Titles cost a small fee. Companies House filings may list property addresses held by companies.
- **Most other countries:** Search by address or lot number only. You may need to prove financial or legal interest, or be a citizen. However, some registries grant access for journalistic reasons -- contact the registry office directly.

### Investigative Techniques

- **Always check addresses in Google Maps.** This tells you whether an address is a real office, a residential property, a mailbox service, or an empty lot. It also reveals neighboring companies.
- **Cross-reference registered addresses:** If a company's registered address is a private residence, it suggests the entity lacks real operational presence.
- **Historical ownership:** Track property transfers over time. A property changing hands between connected entities at below-market value is a red flag.

## US Non-Profit Investigation

Large amounts of money flow through non-profits, foundations, and trusts. In the US, non-profits are required to file their finances in tax returns (Form 990), which are collected by states and aggregated by independent organizations.

### Key Databases

- **ProPublica Nonprofit Explorer** (projects.propublica.org/nonprofits/) -- Searchable database of every US tax-exempt organization's Form 990 filings. Shows revenue, expenses, executive compensation, grants made and received, and board members.
- **GuideStar / Candid** (guidestar.org) -- Similar coverage with additional organizational profiles and financial data. Free basic access, paid tiers for deeper data.
- **527 Explorer** -- Political organizations that are tax-exempt under section 527 of the Internal Revenue Code. Useful for tracing dark money in political campaigns.

### What to Look For in 990s

- **Executive compensation:** Are officers being paid disproportionately?
- **Grants to connected organizations:** Money flowing between entities controlled by the same people.
- **Program expenses vs overhead:** A non-profit that spends most of its money on "administration" and "fundraising" rather than programs may not be what it claims.
- **Sudden revenue changes:** A small non-profit that suddenly receives millions deserves scrutiny.
- **Board members:** Cross-reference against the people and companies in your investigation.

## Court Documents

Legal disputes produce documents that contain information companies would never voluntarily disclose -- including ownership details, financial records, internal communications, and contracts.

### Key Databases

- **PACER** (pacer.psc.uscourts.gov) -- US federal court records. Contains affidavits, evidence, and records from civil and criminal cases. Small per-page fee. Extremely valuable for financial investigations.
- **BAILII** (bailii.org) -- British and Irish Legal Information Institute. Searchable database of UK court verdicts. More limited than PACER but free.
- **Country-specific court databases:** Many countries publish court decisions online. Check OSINT Navigator for country-specific resources.

### What Court Records Reveal

- **Ownership disputes** often include detailed corporate structures as evidence
- **Bankruptcy filings** list all assets and creditors
- **Divorce proceedings** may reveal hidden assets
- **Contract disputes** include the actual contracts between parties
- **Regulatory enforcement actions** detail financial misconduct

## Trade Data

Import/export data reveals supply chains, sanctions evasion, and the movement of goods between entities.

### Key Databases

- **ImportGenius** (importgenius.com) -- US and international import/export records. Shows who is shipping what, from where, to whom.
- **UN Comtrade** (comtrade.un.org) -- International trade statistics by commodity and country. Useful for identifying anomalies in trade flows.
- **Country-specific customs databases:** Some countries publish import/export data. Check OSINT Navigator.

### Investigative Applications

- **Sanctions evasion:** A company under sanctions may be importing/exporting through front companies. Trade data can reveal the real parties.
- **Supply chain investigation:** Trace the movement of goods (timber, minerals, oil) from source to market. Useful for environmental and conflict investigations.
- **Transfer pricing:** Related companies trading goods between jurisdictions at artificially low or high prices to shift profits.

## Art and Antiquities

Art is increasingly used to launder money and move it into legitimate financial streams.

### How Art Laundering Works

- Art can be purchased anonymously at auction
- Values are subjective, making it easy to inflate or deflate prices
- Art can be stored in tax-free freeports (Geneva, Luxembourg, Singapore) indefinitely
- Provenance (ownership history) can be difficult to verify

### Investigative Approaches

The Pandora Papers revealed 1,600+ works of art secretly traded through tax havens. Reporters found dozens of pieces by:
- Searching public sources: books, museum websites, gallery catalogs, art blogs
- Working with art experts to identify and authenticate pieces
- Checking dimensions and other details to confirm specific works
- Tracing provenance through auction records, gallery catalogues, and museum acquisition databases

## Sanctions Databases

Finding a person or company under sanctions is a story on its own. Check:

- **OpenSanctions** (opensanctions.org) -- Aggregated global sanctions data. The best single source.
- **OFAC SDN List** (sanctionssearch.ofac.treas.gov) -- US Treasury's Specially Designated Nationals list.
- **EU Sanctions Map** -- European Union consolidated sanctions list.
- **UN Security Council Consolidated List** -- UN sanctions.

Check all name variations, associated companies, and known aliases. Sanctions data also provides leads: if an associate of your target is sanctioned, that's worth investigating.

## The Extra Tricks

When standard databases don't give you what you need:

### Domain WHOIS
If you can't find ownership through official sources, check who registered the company's website. Use DomainTools (domaintools.com) for historical WHOIS data. The first registrant is often the actual owner.

### Wayback Machine
People often initially put ownership information on a company website but later remove it. Check web.archive.org for archived versions that preserve removed content.

### Ask Locals
Sometimes online research won't get you answers. Reach out to someone who lives in or near the country of registration. Local journalists, lawyers, or civil society groups often know things that aren't online. OCCRP, GIJN, and ICIJ can connect you with local contacts.

### OCCRP ID
id.occrp.org -- A global index of registries from 180+ countries in one place. Use this as your starting point when you need to find the right registry for a specific country.

### Learn the Jurisdiction
Look at companies and websites that provide incorporation services (e.g., Systemday at systemday.com). They explain how company formation works in each jurisdiction, which helps you understand what records should exist and where.

## Credits & Attribution

Primary sources:
- Jelter, "Follow the Money" (presentation). Property registries, non-profits, court documents, and "extra tricks" methodology.
- Miranda Patrucic & Jelena Cosic, "Introduction to Investigative Journalism: Following the Money," GIJN, November 2024. Licensed under CC BY-ND 4.0.

Tool discovery: OSINT Navigator (navigator.indicator.media)
