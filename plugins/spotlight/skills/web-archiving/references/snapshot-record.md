# Snapshot record — first-party web archive (Tier 0)

Canonical schema for a first-party page snapshot captured by the `web-archiving` skill's
**Tier 0** (dev-browser). **Owner-of-record: Spotlight** (`tools/spotlight/skills/web-archiving/`).
Mycroft mirrors this file byte-for-byte — change Spotlight first, then re-sync. A pre-commit
diff between the two copies guards against drift.

## Content-addressed storage

Each captured artifact is stored named by the SHA-256 of its bytes: `{sha256}.{ext}`. Identical
bytes collapse to one file (dedup); a filename **is** its integrity hash.

- MHTML archive:        `<sha256>.mhtml`  — `multipart/related`, subresources inlined
- Full-page screenshot: `<sha256>.png`
- Raw HTML (fallback only): `<sha256>.html`

Storage dir: Spotlight → `{CASE_DIR}/evidence/snapshots/`; Mycroft → `cases/{project}/research/archived/snapshots/`.

## Snapshot record (chain-of-custody)

Emitted alongside the artifacts — in the archived file's YAML header and the skill's log:

```jsonc
{
  "url": "https://example.com/article",
  "captured_at": "2026-06-17T14:22:00Z",   // UTC, ISO-8601
  "capturer": "dev_browser",                // dev_browser | firecrawl  (UNDERSCORE — matches the evidence enum)
  "http_status": 200,
  "headers": { "content-type": "text/html; charset=utf-8" },  // response headers
  "condition": "load",                      // load | domcontentloaded | networkidle | selector:<css>
  "artifacts": [
    { "type": "mhtml",      "sha256": "<64hex>", "path": "evidence/snapshots/<sha>.mhtml" },
    { "type": "screenshot", "sha256": "<64hex>", "path": "evidence/snapshots/<sha>.png" }
    // rawhtml ONLY on the firecrawl fallback path — MHTML supersedes it on the dev_browser path
  ],
  "access_wall": false,                     // true ⇒ a login/paywall was captured, NOT the content
  "error": null,
  "third_party": { "wayback": "https://web.archive.org/web/…|null", "archive_today": "…|null" }
}
```

## Mapping to evidence-bundle.json (Spotlight)

Tier 0 does **not** invent new evidence-bundle fields. Map the snapshot onto a schema-conforming
`evidence_item` (`schemas/evidence-bundle.schema.json`) using **existing** fields, so it validates
(`scripts/validate-case.py`) and is carried into the C2PA-signed `provenance-manifest.json`:

| `evidence_item` field          | snapshot value |
|--------------------------------|----------------|
| `id`                           | `snap-<first 8 hex of mhtml sha>` |
| `query_or_task`                | `"web-archiving Tier-0 snapshot: <url>"` |
| `acquisition_method`           | `"dev_browser"` (or `"firecrawl"` on fallback) |
| `source_url`                   | the URL |
| `accessed`                     | `captured_at` |
| `sha256`                       | the **MHTML** artifact hash (64 hex) |
| `raw_path`                     | `evidence/snapshots/<sha>.mhtml` (or `.html` on fallback) |
| `screenshot_path`              | `evidence/snapshots/<sha>.png` |
| `extraction_confidence`        | `high` normally; `medium` if `access_wall` |
| `human_verification_required`  | `false`; `true` if `access_wall` |
| `notes`                        | `"MHTML+screenshot; condition=<c>; screenshot_sha256=<h>"` |

`build-provenance-manifest.py` hashes the files referenced by `raw_path`/`screenshot_path`, so
**both** artifacts are tamper-evident inside the signed manifest (not just the self-reported `sha256`).

## Rules

- **Never store an access wall as content evidence** — set `access_wall: true` + `human_verification_required: true`.
- **Content-addressing is mandatory** — the stored filename must equal the SHA-256 of the bytes.
- **Tier 0 never suppresses Tier 1/2** — third-party archive URLs still go in `third_party`.
