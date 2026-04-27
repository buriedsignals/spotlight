# Unpaywall Integration

Unpaywall finds legal open-access copies of academic papers when you have a DOI.

## When To Use

Use this integration from `content-access` when:

- The source is an academic paper.
- You have a DOI.
- `integrations/preflight.py` reports `unpaywall` as green.

If the integration is red, skip Unpaywall and continue with CORE, Semantic Scholar, archive copies, and author contact.

## Environment

Requires:

```bash
UNPAYWALL_EMAIL=you@example.com
```

This email is sent to Unpaywall as a fair-use contact identifier. It is not an API key.

## Request

```bash
curl "https://api.unpaywall.org/v2/{DOI}?email=${UNPAYWALL_EMAIL}"
```

## Output Handling

Read `best_oa_location.url` first. If it is empty, inspect `oa_locations[]` for a legal open-access URL or PDF URL.

If a URL is found, fetch it into the case research folder and record:

```json
{
  "access_method": "open_access",
  "access_notes": "Legal open-access copy found via Unpaywall DOI lookup"
}
```

Save raw API responses to:

```text
cases/{project}/research/unpaywall-{doi-slug}-{timestamp}.json
```

## Sensitive Mode

Unpaywall is a remote API. Do not call it when sensitive mode strips remote fetch/search behavior. Use already saved responses if present, or continue with local evidence.
