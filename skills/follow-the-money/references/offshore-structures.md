# Offshore Structures & Secrecy Jurisdictions

How offshore and secrecy jurisdictions work, how to investigate companies registered there, and how to use leaked databases to pierce corporate secrecy.

## When to Use This

Your corporate traversal (see `corporate-ownership.md`) has led to a holding company or shareholder registered in a jurisdiction known for financial secrecy -- BVI, Cayman Islands, Panama, Luxembourg, etc. Or you're investigating someone suspected of using offshore structures to hide wealth.

## How Offshore Jurisdictions Work

Offshore jurisdictions are countries or territories that have specialized in creating favorable conditions for company incorporation: low or zero taxes, minimal disclosure requirements, and strong privacy protections for owners.

**Why they exist:** For a few hundred dollars, you can create a company in Panama with a minimum capital of USD 1,000. This company can be the shareholder of a company in Germany, and because the financial activity is in Germany while the owners are in Panama, you can minimize taxes. The same structure provides secrecy -- Panama's registry won't tell you who the real owner is.

**Legitimate vs illegitimate use:** Offshore structures serve legitimate purposes (international trade, tax planning, asset protection). But they are also used to hide the proceeds of corruption, evade sanctions, launder money, and conceal conflicts of interest.

## Common Offshore Patterns

### Layered Holding Structures
The most common pattern: a company in a transparent jurisdiction (UK, US, Germany) is owned by a holding company in a secrecy jurisdiction (BVI, Cayman Islands), which is in turn owned by another entity in a different secrecy jurisdiction. Each layer adds opacity.

### Shell Companies
Companies with no real business operations, employees, or significant assets. Their sole purpose is to hold money, assets, or ownership stakes. Shell companies are the building blocks of layered structures.

### Trust Structures
Trusts (legal in the US and in offshore jurisdictions) allow assets to be held by a trustee on behalf of beneficiaries. The trust itself may not appear in company registries. In the US, trusts are increasingly used to hold property anonymously.

### Nominee Arrangements
A nominee director or shareholder is officially listed but acts on instructions from the real owner. Formation agents in secrecy jurisdictions routinely provide nominee services. If the same nominee name appears on hundreds of companies, they are a professional nominee -- the real owner is hidden.

## Investigating Offshore Entities

Offshore registries usually do not have detailed company information available online or by request. Your main investigative avenues are leaks, court documents, and the subsidiary trick.

### ICIJ Offshore Leaks Database

The single most important resource for offshore investigation. Search at offshoreleaks.icij.org.

**What it contains:** ~810,000 offshore entities from:
- **Panama Papers** (2016) -- 11.5 million documents from Mossack Fonseca, a Panamanian law firm
- **Paradise Papers** (2017) -- 13.4 million documents from Appleby and Asiaciti Trust
- **Pandora Papers** (2021) -- 11.9 million documents from 14 offshore service providers
- **Bahamas Leaks** (2016) -- corporate registry of the Bahamas
- **Offshore Leaks** (2013) -- the original trove from BVI and other jurisdictions

**How to search:** By name (person or company), jurisdiction, intermediary, or address. Cross-reference every name from your corporate traversal against this database.

**Key insight:** Even a decade after publication, not every story has been reported. New findings regularly emerge from these databases.

### OCCRP Aleph

Search at aleph.occrp.org. Aleph aggregates public records, leaked documents, and investigation data from millions of sources into a single searchable platform.

- **Free to browse** anonymously or with a user account
- **Friend of OCCRP** status (apply at requests.occrp.org/register) gives professional journalists wider access
- Contains data from company registries, court records, gazettes, and leaked documents worldwide
- Particularly strong on post-Soviet states, Balkans, and Central/Eastern Europe

### OffshoreAlert

offshorealert.com -- monitors offshore and onshore courts, regulatory actions, and other sources for red flags in high-value international finance. Provides email summaries of new findings. Particularly useful for finding court cases involving offshore entities.

### OpenSanctions

opensanctions.org -- aggregated global sanctions data. Check company names, aliases, directors, and associated entities against sanctions lists (OFAC, EU, UN). Finding a person or company under sanctions is a story on its own. Also useful for identifying money laundering leads and sanctions evasion patterns.

## The Subsidiary Trick

One of the most powerful techniques for piercing offshore secrecy:

**Requirements to file ownership vary by country.** A multinational with a holding company in Panama but a subsidiary in Ireland must file UBO information (down to a natural person) in Ireland, because Irish law requires it.

**The method:** When you find an opaque offshore parent, search for subsidiaries in transparent jurisdictions (UK, Ireland, EU countries, Australia). The subsidiary's filings in the transparent jurisdiction may reveal the ownership of the entire structure.

This works because companies need operational presence in the countries where they do business, and those countries may require full ownership disclosure.

## Domain Registration as Ownership Signal

When official and corporate registries reveal nothing, check who registered the company's website:

- **DomainTools** (domaintools.com) -- historical WHOIS data showing every registrant change over time
- **Current WHOIS** -- may show the owner's name, especially for older registrations before privacy protection became standard
- **Wayback Machine** (web.archive.org) -- people often initially put ownership information on a company website but later remove it. Archived versions preserve it.

The first registrant of a company's domain is often the actual owner or someone close to them.

## Financial Statement Red Flags

When you can access a company's financial statements (via registry filings, court documents, or annual reports), watch for:

- **High receivables:** The company has sold goods or services but hasn't collected payment. Could mean money is being stashed elsewhere.
- **Related-party transactions:** Payments to companies connected to directors or shareholders. One investigation found a UK company paying a "success fee" to a company later linked to a president's son-in-law.
- **Loans from connected shells:** A company owes money to a supplier, then takes a loan from a connected shell company to avoid taxes, never repaying the shell.
- **Inflated service fees:** Payments for vague "consulting" or "management" services to entities in secrecy jurisdictions.
- **Revenue/expense mismatches:** Income that doesn't match the company's stated business activity.

## Working with Leaked Documents

When searching ICIJ or Aleph databases:

1. **Search every name** from your investigation -- target company, officers, shareholders, formation agents, registered addresses
2. **Search variations** -- different transliterations, maiden names, abbreviations
3. **Note the intermediary** -- the law firm or trust company that created the offshore entity. They often create multiple entities for the same client.
4. **Map the formation agent** -- if your target used Mossack Fonseca for one entity, search for other Mossack Fonseca entities connected to the same officers or addresses
5. **Check the dates** -- when were entities created? Do they coincide with known events (contract awards, elections, sanctions)?

## Credits & Attribution

Primary sources:
- Jelter, "Follow the Money" (presentation). Offshore structures, the subsidiary trick, and domain registration techniques.
- Miranda Patrucic & Jelena Cosic, "Introduction to Investigative Journalism: Following the Money," GIJN, November 2024. Licensed under CC BY-ND 4.0.
- Derek Bowler, "Tracing Beneficial Ownership with OSINT for Financial Crime," EBU Eurovision News Spotlight, December 2025.

Tool discovery: OSINT Navigator (navigator.indicator.media)
