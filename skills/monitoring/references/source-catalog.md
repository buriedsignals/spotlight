# Passive Feed Source Catalog

Registry of passive monitoring sources now owned by **Mycroft**.

Spotlight no longer ships or extends these source implementations directly. It may still reference them when deciding whether to register a Mycroft topic for a case.

## Sources

| Source ID | Name | Category | Notes |
|---|---|---|---|
| `gdelt` | GDELT Document API | news | Global keyword-driven news search |
| `rss_investigative` | Investigative RSS bundle | news | Bellingcat, ICIJ, Crisis Group, and similar outlets |
| `rss_regional` | Regional RSS bundle | news | Regional and global feeds for underreported geographies |
| `gdacs` | GDACS disaster alerts | disaster | Crisis and humanitarian monitoring |
| `acled` | ACLED conflict events | conflict | Requires `ACLED_API_KEY` and `ACLED_EMAIL` |

## Ownership rule

- Add or modify passive feed sources in **Mycroft**
- Do not add new feed-source code to Spotlight
- In Spotlight, use monitoring recommendations plus Mycroft topic registration instead

## When Spotlight should care

Spotlight should use this catalog only to decide whether a recommendation benefits from:

- a passive Mycroft topic;
- a durable coJournalist scout;
- both.
