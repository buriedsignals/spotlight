// The page shell. It carries no case text: the page fetches the case as JSON
// and inserts it with textContent. The CSP nonce is placed on the shell's own
// two tags here, never by searching and replacing across a rendered page.
import { readFileSync } from "node:fs";

const APP_JS = readFileSync(new URL("./page/app.js", import.meta.url), "utf8");
const APP_CSS = readFileSync(new URL("./page/app.css", import.meta.url), "utf8");
const NONCE = /^[A-Za-z0-9_-]{16,}$/;
if (/<\/script/i.test(APP_JS) || /<\/style/i.test(APP_CSS))
  throw new Error("page assets must not close their own element");

// JSON safe to place inside a <script> element: no "<", ">" or "&" survives.
export function inlineJson(value) {
  return JSON.stringify(value)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

export function renderShell({ nonce, boot = {} }) {
  if (!NONCE.test(nonce ?? "")) throw new Error("the shell needs a fresh CSP nonce");
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Spotlight review</title>
<style nonce="${nonce}">${APP_CSS}</style>
</head>
<body>
<a class="skip" href="#main">Skip to the review</a>
<main id="main" tabindex="-1">
<h1 id="title">Spotlight review</h1>
<p id="status" role="status" aria-live="polite">Loading the review…</p>
<div id="alert" role="alert" hidden></div>
<div id="app" aria-busy="true"></div>
</main>
<script type="application/json" id="boot">${inlineJson(boot)}</script>
<script type="module" nonce="${nonce}">${APP_JS}</script>
</body>
</html>
`;
}
