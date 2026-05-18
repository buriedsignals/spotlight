# browser-harness — Browser Acquisition Fallback

Browser Harness is Spotlight v2's preferred browser fallback after Firecrawl cannot acquire a needed source.

## Use After Firecrawl

Do not start here. First use `search` or `fetch`. Then run the missing-source gate:

- What source was requested?
- What did Firecrawl return?
- What artifact path was saved?
- What is still missing?
- Does the remaining gap require a browser, authenticated session, download, screenshot, visual verification, iframe/shadow DOM handling, or manual human verification?

Use Browser Harness only when the static acquisition gap is material.

## Use For

- public-record portals,
- registry lookup flows,
- JS-rendered document tables,
- file downloads,
- screenshots and visual verification,
- iframes and shadow DOM,
- hostile navigation,
- legally appropriate authenticated or local-browser contexts.

## Evidence Output

Every Browser Harness acquisition should update `cases/{project}/data/evidence-bundle.json` with:

- `acquisition_method: "browser_harness"`,
- source URL,
- start/end or access timestamp,
- raw HTML/markdown path where available,
- screenshot path where relevant,
- downloaded document path and SHA-256 where relevant,
- missing-source gate notes,
- validation checks,
- `human_verification_required` when ambiguity remains.

Store screenshots and downloads under `cases/{project}/evidence/`.

## Shell Safety

Invoke `shell-safety` before passing task text, URLs, or output paths into Browser Harness. Prefer task files or JSON over interpolating long task strings into shell commands.

Pattern:

```text
write-file("cases/{project}/research/browser-harness-task.json", <serialized task JSON>)
execute-shell("browser-harness run --task-file cases/{project}/research/browser-harness-task.json --output cases/{project}/research/browser-harness-result.json")
```

## Do Not Automate

- Access-control bypass.
- CAPTCHA or rate-limit evasion.
- Secret, cookie, or token capture.
- One-off pixel-coordinate scripts.
- Bulk social scraping before shell-safety and legal review.
