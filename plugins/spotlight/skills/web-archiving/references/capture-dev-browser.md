# Tier-0 capture recipe — dev-browser (first-party snapshot)

**Owner-of-record: Spotlight.** Mycroft mirrors this recipe; change here first. Produces a
first-party MHTML archive + full-page screenshot + response headers, content-addressed by
SHA-256. No Docker/Postgres/service — real Chrome via the local `dev-browser` CLI.

> Verified 2026-06-17: `Page.captureSnapshot {format:'mhtml'}` over a CDP session yields a
> valid `multipart/related` archive with subresources inlined (Wikipedia: 756 KB, 48 MIME
> parts, 3 base64 binaries, 34 ms); `page.screenshot({fullPage:true})` yields a full PNG.

## Preconditions

- `dev-browser` on PATH (`command -v dev-browser`). If absent, use the firecrawl fallback
  (see SKILL.md) — do not block.
- Sensitive mode: do **not** run a live capture. The `web-archiving` skill is disabled in
  sensitive mode; if reached, fall back to Tier 1 (Wayback) or require explicit egress approval.

## Step 1 — validate the URL (shell-safety)

```
execute-shell: python3 scripts/spotlight_safe.py validate-url "{URL}"
```

Only after this passes, embed the URL into the script below **as a JSON-quoted string literal**
(never raw concatenation).

## Step 2 — write the capture script

The dev-browser sandbox is QuickJS (no `fs`/`require`/`fetch`); file output goes through the
`writeFile` / `saveScreenshot` helpers, which write to `~/.dev-browser/tmp/` and **return the
absolute path**. Use a unique tag per capture (helpers open `O_TRUNC` — a fixed name silently
overwrites a concurrent capture). Read back the returned paths; do not reconstruct them.

```
write-file: path={CASE_DIR}/research/dev-browser-archive.js, content=
```
```js
// URL is embedded as a JSON-quoted literal AFTER validate-url passes.
const url = "{URL}";
const condition = "load"; // load | domcontentloaded | networkidle ; or wait for a selector
const tag = String(Date.now());           // unique per capture; avoids O_TRUNC collisions
const out = { url, captured_at: new Date().toISOString().replace(/\.\d+Z$/, "Z") };

const page = await browser.getPage("archive-" + tag);
const resp = await page.goto(url, { waitUntil: condition });
out.http_status = resp ? resp.status() : null;
out.headers = resp ? resp.headers() : {};
out.condition = condition;

// Access-wall heuristic — never store a login/paywall as content evidence.
const title = (await page.title()).toLowerCase();
const bodyText = (await page.evaluate(() => document.body ? document.body.innerText.slice(0, 4000) : "")).toLowerCase();
const wallMarkers = ["log in to continue", "sign in to continue", "subscribe to read", "create a free account", "this content is for subscribers", "please log in", "register to continue"];
out.access_wall = [401, 402, 403].includes(out.http_status) || wallMarkers.some(m => title.includes(m) || bodyText.includes(m));

// Full-page screenshot
out.tmp_png = await saveScreenshot(await page.screenshot({ fullPage: true }), "archive-" + tag + ".png");

// MHTML via CDP session (Chromium) — subresources inlined
const client = await page.context().newCDPSession(page);
const snap = await client.send("Page.captureSnapshot", { format: "mhtml" });
out.tmp_mhtml = await writeFile("archive-" + tag + ".mhtml", snap.data);

await browser.closePage("archive-" + tag);
console.log(JSON.stringify(out));
```

## Step 3 — run the capture

```
execute-shell: dev-browser --headless --timeout 90 run {CASE_DIR}/research/dev-browser-archive.js
```

Parse the single JSON line from stdout. On a Playwright/navigation error, dev-browser exits
non-zero or the JSON carries no `tmp_mhtml` — record the snapshot with `error` set and proceed
to Tier 1/2 (do not abort the archive).

## Step 4 — content-address into the case (hash AFTER copy)

For each of `tmp_mhtml` (ext `mhtml`) and `tmp_png` (ext `png`):

```
execute-shell: python3 scripts/spotlight_safe.py sha256 "{TMP_PATH}"      # -> <sha>
execute-shell: mkdir -p {CASE_DIR}/evidence/snapshots
execute-shell: cp "{TMP_PATH}" {CASE_DIR}/evidence/snapshots/<sha>.<ext>
execute-shell: rm -f "{TMP_PATH}"
```

The stored filename equals the artifact's SHA-256 (content-addressed; identical bytes dedup).

## Step 5 — record + chain of custody

Build the snapshot record (`references/snapshot-record.md`) and map it onto an
`evidence-bundle.json` `evidence_item` (existing fields: `sha256`=MHTML hash, `raw_path`=the
`.mhtml`, `screenshot_path`=the `.png`, `acquisition_method`="dev_browser", `extraction_confidence`
= `high` or `medium` if `access_wall`, `human_verification_required` = `access_wall`). Then run
Tier 1 (Wayback) + Tier 2 (Archive.today) and fill `third_party`. Write the chain-of-custody
block (SKILL.md) including `snapshot_sha256` + `snapshot_path`.
