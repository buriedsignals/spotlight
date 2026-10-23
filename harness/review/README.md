# Spotlight review pages

A local web page where the member reviews and decides each human gate of a
case: Methodology, Investigation gate, Report and (solo) Ingest. Node only, no
dependencies; it calls Spotlight's Python orchestration seam for every read and
write.

## How a decision is made

1. The harness (or the MCP tool below) starts the server for one case and opens
   the member's browser. The page address is never returned to the model.
2. The page shows, in order: blocking integrity failures, flags that need a
   disposition, the selection, then the digest and the decision buttons.
3. Every mark, disposition, selection change, note and the member's name is
   saved at once to `data/<gate>-review.json` by
   `scripts/spotlight-orchestration.py review-edit`, under the case lock. The
   file keeps each suggestion and the member's final value separately and counts
   how many suggestions were accepted unchanged.
4. Approve stays unavailable while a blocking failure or a required disposition
   is open. When the member decides, the server sends the digest the page showed
   plus a one-time nonce it holds. The Python transition refuses a changed digest
   (the page reloads and asks again), an unknown or spent nonce, and open
   blockers, then spends the nonce and records the decision in the same atomic
   write. The receipt binds the review digest and the nonce's hash.
5. The resolver counts a gate decision only when `data/review-nonces.json` backs
   it, so a decision typed into `data/orchestration.json` does not advance the
   case.

The command-line adapter (`spotlight-orchestration.py approve`, `decide-report`,
`decide-ingest`) still works without a nonce. It records its decisions on its
own `direct` channel, which the receipt shows.

## Security model

- Binds `127.0.0.1` on a random port; checks `Host` on every request and
  `Origin` on every POST.
- The URL fragment carries a one-time capability, swapped on first use for an
  `HttpOnly; SameSite=Strict` session cookie. Reopening issues a fresh
  capability and ends the previous page's session.
- Every HTML response carries a new CSP nonce, placed only on the shell's own
  `<script>` and `<style>` tags. The shell contains no case text; the page reads
  the case as JSON (with `<`, `>` and `&` escaped) and inserts it with
  `textContent`.
- `Cache-Control: no-store`; the session closes after 45 minutes idle and 8
  hours overall.
- The server runs in a child process whose environment has keys, tokens and
  loader hooks removed. Its control channel and logs carry no case text.
- Accepted limit: a process running as the same user can still call the seam
  and record a decision, as with any local tool.

## Opening the page

From a harness:

```js
import { createReviewOpener } from "./harness/review/open.mjs";
const openSpotlightReview = createReviewOpener();
await openSpotlightReview({ caseDir: "/absolute/path/to/case" });
// → { status: "opened" | "no-pending-gate" | "opener-unavailable" | ... }
```

It starts the server while the resolver reports a pending gate, or reopens a
running one with a fresh capability.

From a chat app (subscription mode), register the stdio MCP server:

```text
command: node
args:    ["<checkout>/harness/review/mcp.mjs"]
env:     SPOTLIGHT_CASES_ROOT=<case workspace root>   (optional; refuses cases outside it)
```

It exposes one tool, `open_spotlight_review({case_dir})`, which returns "page
opened" text and no URL. The member says in chat when they are done, and the
agent resolves the case, which reads the decision from disk.

## Tests

```bash
npm test --prefix harness/review
python3 tests/review-gate-check.py
```
