# dev-browser — Task-Specific Browser Automation

`dev-browser` is Spotlight's browser automation tool for specific investigative
tasks that cannot be handled by ordinary search or static scraping. Use it only
when the task requires dynamic page interaction, registry search forms,
authenticated portals, downloads, screenshots, rendered tables, visual
verification, or multi-step UI navigation.

## When To Use

Use `dev-browser` when:

- Firecrawl returns an incomplete page because the source is JS-rendered.
- A registry or database requires search forms, clicks, filters, or pagination.
- The journalist needs screenshots, visual state, or rendered-table evidence.
- A portal requires the journalist to authenticate in a browser.
- A download button or document viewer must be operated through the page.

Do not use it for bulk scraping or routine page acquisition. Use Firecrawl
first and preserve the browser path for material acquisition gaps that require
actual browser automation.

## Usage Pattern

Invoke `shell-safety` before constructing commands from URLs, user input,
scraped values, or file paths.

Write small scripts and run them with `dev-browser`. Scripts execute inside the
dev-browser QuickJS sandbox with a preconnected `browser` global and Playwright
Page API.

```text
write-file("{CASE_DIR}/research/dev-browser-task.js", "<script>")
execute-shell("dev-browser --timeout 30 run {CASE_DIR}/research/dev-browser-task.js")
```

For unattended browser workflows:

```text
execute-shell("dev-browser --headless --timeout 45 run {CASE_DIR}/research/dev-browser-task.js")
```

For journalist-controlled authenticated sessions, omit `--headless` and use a
stable browser/page name so the session can be resumed:

```text
execute-shell("dev-browser --browser spotlight-{project} --timeout 60 run {CASE_DIR}/research/dev-browser-task.js")
```

Always clean up a named browser when the session ends. Put the trap in the
launcher or shell session that owns the browser name:

```bash
browser_name="spotlight-{project}"

cleanup_browser() {
  trap - EXIT INT TERM
  bash /Users/tomvaillant/buried_signals/scripts/dev-browser-stop-browser.sh "$browser_name" || true
}

trap cleanup_browser EXIT INT TERM
dev-browser --browser "$browser_name" --timeout 60 run {CASE_DIR}/research/dev-browser-task.js
```

The helper stops only that named browser and is safe to call when it has
already stopped. Do not use `dev-browser stop` in a session trap: that stops
the daemon and every managed browser, including unrelated active work.

## Evidence Handling

Every browser acquisition should update `{CASE_DIR}/data/evidence-bundle.json`
with:

- `acquisition_method: "dev_browser"`
- source URL
- accessed timestamp
- task/script path
- screenshot paths when visual evidence matters
- downloaded document paths and hashes when applicable
- missing-source gate answer explaining why Firecrawl was insufficient
- human verification flag for authenticated or account-specific views

Save structured dev-browser output into `{CASE_DIR}/research/` when the
script returns extracted text, links, IDs, or table rows. Save screenshots and
downloaded files under `{CASE_DIR}/evidence/` when possible; dev-browser's
own temp outputs should be copied or recorded before final review.

## Minimal Script Shape

```js
const page = await browser.getPage("case-main");
await page.goto("https://example.org/search");
const snapshot = await page.snapshotForAI({ track: "search" });
console.log(JSON.stringify({
  url: page.url(),
  title: await page.title(),
  snapshot: snapshot.full
}, null, 2));
```

Prefer `page.snapshotForAI()` for structure discovery, Playwright locators for
actions, and screenshots when the visual state is itself evidence.

## Sensitive Mode

In sensitive mode, do not use `dev-browser` against live external sites unless
the journalist explicitly approves browser egress. It can still be used against
local HTML, cached archives, or already acquired files.
