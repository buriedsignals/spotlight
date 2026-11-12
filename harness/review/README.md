# Spotlight review pages

A local web page where the member reviews and decides each human gate of a
case: Methodology, Research, Investigation gate, Report and (solo) Ingest. Node only, no
dependencies; it calls Spotlight's Python orchestration seam for every read and
write.

## How a decision is made

1. The harness (or the MCP tool below) starts the server for one case and opens
   the member's browser. The page address is never returned to the model.
2. The page opens on what the member decides. A closed strip names any
   blocking integrity failures, then come the findings with their fact-check
   table (on the Research page, the clues and their marks), the flags that need
   a disposition and the selection (the Report page shows the draft that
   publishing renders), then the digest and the decision buttons (in newsroom
   mode, the member's name first). Run details, sources, leads and passages sit in closed blocks.
3. Every mark, disposition, selection change, note and the member's name is
   saved at once to `data/<gate>-review.json` by
   `scripts/spotlight-orchestration.py review-edit`, under the case lock. The
   file keeps each suggestion and the member's final value separately and counts
   how many suggestions were accepted unchanged.
4. A solo decision is recorded under the fixed name `Member`, and the page asks
   for no name. In newsroom mode every decision needs the member's name, which
   is recorded with it. A name counts only for the decision it was entered for:
   when a follow-up reopens the gate, the page starts with an empty name and
   shows the earlier one as history. The Report page shows each finding's fact-check verdict and
   confidence, and marks the ones the fact-check did not verify. Approve
   stays unavailable while a blocking failure or a required disposition is
   open. The Investigation gate also offers "Request follow-up" with a
   required note: it sends the case back to Investigation, on the same nonce
   and digest rules, and open flags or failures do not block it. When the
   member decides, the server sends the digest the page showed
   plus a one-time nonce it holds. The Python transition refuses a changed digest
   (the page reloads and asks again), an unknown or spent nonce, and open
   blockers, then spends the nonce and records the decision in the same atomic
   write. The receipt binds the review digest and the nonce's hash.
5. The resolver counts a gate decision only when `data/review-nonces.json` backs
   it, so a decision typed into `data/orchestration.json` does not advance the
   case.

The command-line adapter (`spotlight-orchestration.py approve`, `decide-report`,
`decide-ingest`, `request-follow-up`) still works without a nonce. Approvals
and report and ingest decisions are recorded on its own `direct` channel, which
the receipt shows; a name (`--decided-by`, `--decided-at`) is optional there.
A follow-up without a nonce is the agent's: it is recorded on the `agent`
channel with the requesting role (`--requested-by-role`, set by the harness,
default `orchestrator`) and the time, and it refuses a member name. The page
shows "Recorded through: the agent (orchestrator)"; a follow-up recorded before
follow-ups said who asked shows as not recorded.

## Case passages

Every gate page shows, after the gate's content, the passages most worth
reading next to each claim it shows: the findings' claims (none before
Investigation, so the Methodology page is usually empty) or, at Ingest, the
analyst's proposed claims. The page asks for them once per gate
(`/api/passages`, `spotlight-orchestration.py review-passages <gate>`), after
the view, so a slow search never delays the page or a decision. Case search
(`integrations/case_search.py` over Engine's `bsig wiki case-search`) ranks
the case's own `research/` captures for each claim and leaves out Spotlight's
bookkeeping files and link-list chunks. It is retrieval only: it is not part of
the digest and never sets a mark, a disposition or a link. Passage text is
inserted as text. Without Engine, or with Engine's case-search switch off,
the page shows only "Case search unavailable." When the search stops early
(Engine fails or the overall deadline passes), the claims already searched keep
their passages and the rest show "Not searched".

## The Research page

The Research gate page (`scripts/spotlight_orchestration/research_view.py`)
shows, after any blocking failure:

1. the research mode (local lanes only, OpenRouter swarm, subscription
   subagents or newsroom), the round's spend against its budget and the topics
   it left out or refused (`data/leads.json`, `research/rounds.json`), and the
   triage notice: while the decision-model acceptance test has not passed,
   triage only flags;
2. the clues, in ranking order (`research-merge.py --rank`): the rule clues of
   `data/clues.json` and the premise, new-actor and change clues triage raised.
   Each is marked pursue or dismiss; watchlist (K6) and premise (C1) clues are
   always pursued and cannot be dismissed. Every clue needs a mark before
   approval;
3. each direction with its mark (keep or drop; a new direction is added, by
   the investigator's suggestion or by the member), its gap and its remaining
   leads;
4. the circumstantial leads (about another person, company or event than the
   brief's: kept and labelled, never set aside for it), the leads triage
   flagged, the leads set aside, the snippet-only leads and the unverifiable
   leads, each in its own section, so none is hidden inside a
   direction. Every lead appears once outside the clues.

The investigator's suggested marks (`data/research-suggestions.json`, shape in
`research_view.py`) prefill the marks; the review file keeps each suggestion
beside the member's final mark, under the `clue:<id>` and `direction:<id>`
targets the amendment check (D8) reads. The page digest also covers the
triage, suggestions and round ledger the page shows, so an approval binds what
the member saw; the receipt binds the leads, the clues and the saved review.

## Entity picks (solo Ingest)

For each entity mention in the batch with no strong identifier, the Ingest
page asks Engine's register for candidates (`bsig wiki resolve`, the mention's
name and type on stdin, against the solo wiki workspace) and lists each
candidate's id, label and type as text. The member picks one or "None of
these" (stored as `false`, never an id); the server accepts only an id it
offered for the digest the page shows, and asks for a reload otherwise. The
pick is an `entity:<mention>` item in `data/ingest-review.json`, so the
approval digest binds it, and the wiki write links the mention to it as
approved (an exact register link found then still wins). The page shows up
to 50 mentions and 10 candidates each and says how many more it left out. Without Engine, with a bsig that lacks `resolve`,
with no candidates or in newsroom mode the section does not appear. Lookups
run four at a time, each within 10 s and all within 30 s; a failed or late
lookup marks only that mention ("Could not look up") and the gate stays usable.

## Material intake

A second view, opened with or without a pending gate, for the material a
journalist brought to the case (`scripts/intake.py`). Each item is an expandable
section; the first item not yet approved is open, and approving one closes it,
marks it Approved and opens the next. Inside an item, one folded line says what
is recorded about it and its rights (prefilled, for example "Dock worker 1
(pseudonym) · yes, not attributed by name · direct quotes not allowed · consent
spoken, on the recording"). Opening it shows six plain-language fields: kind of
material, how the source is shown (a pseudonym or a real name), whether it may be
published, direct quotes, how consent was given and who holds the rights. They
apply to every claim of the item; there is no per-claim or per-passage rights
control. One button shows the words around every passage of the item, taken
from the file the passage came from; no line numbers are shown. Each claim is a
card with a tick box (ticked means keep), its sentence (click to rewrite it in
place), its structure as tags (who, did, what, from the claim's statement) and
its passage. A claim the model suggested leaving out (`leave_out` on the claim)
arrives unticked and dimmed with the reason. At the bottom: how many are kept
and left out, and one button, "Approve N claims".

The routes, all POST under `/api/intake/`, with the same capability, Host and
Origin checks as every other route:

- `view` returns `intake.py view`: the register with each record's plain
  choices and summary, the choices offered, the passages with the words around
  them, the claims and the counted approvals.
- `rewrite` saves a claim's new sentence through `intake.py decide ... edit`
  (the proposition only; the claim's decision is left as it was). The page sends
  it when the sentence loses focus and has changed.
- `approve-material` approves one item in a single request. The server runs, in
  order: `intake.py material-rights` (the item's rights, written to the record
  while it is a draft and then to every passage of the item), `intake.py decide`
  for each claim (keep when ticked, drop when not), `confirm` unless the record
  is already confirmed, and `approve`. It checks first that the page sent exactly
  the claims the item has and that at least one is ticked. The first refusal
  stops it: the answer is 422 with the reason and the step that refused
  (`rights`, `claims`, `confirm` or `approve`), what earlier steps saved stays
  saved, and nothing after the refusal runs. A closed tab does not stop a
  request already received.

The page sends no digest and no code. For each of `confirm` and `approve` the
server reads the digest from `intake.py view` after the earlier steps are saved,
asks `intake.py code` for a one-time code for that material and action, keeps
it in that call, and passes both to the command; the page never receives it.
The code is stored hashed in the case's nonce ledger and spent with the
decision. The view lists a record whose confirmation, or an approval whose code
and digest the ledger does not back, as not yet decided. The words around a
passage are read by `intake.py view` only from plain files under the case's
`intake/` folder, and only while the file still has the bytes the passage was
taken from. The solo edition records the fixed `Member`; the newsroom edition
asks for a name once at the top of the page, requires it before approving, and
`intake.py decider` refuses a blank one.

## Wiki changes

When Engine is installed, the page also lists the wiki writes an agent staged
(`bsig wiki staged`): each change's summary, files and diff, shown as text.
The member applies or discards each one. Agents stage wiki writes; they never
apply them. The server takes a one-time nonce per change from
`bsig wiki staged-nonce`, keeps it in-process and hands it to
`staged-apply`/`staged-discard` on stdin with the digest the page showed. A
change edited since the page loaded is refused and the list reloads; a stale
change offers only Discard. Without Engine, or with a bsig that lacks these
verbs, the view does not appear; any other Engine failure shows as a warning
and the gate stays usable. With no gate pending, the page shows only
this view; it is opened on demand by `open_spotlight_wiki_changes` (below).

The limit depends on the runtime. In Pi mode agents cannot run `bsig`, so
confirming on this page is a real boundary. In subscription mode an agent with
a shell could call `staged-nonce` and `staged-apply` itself: there the page is
a speed bump, not a boundary.

## Security model

- Binds `127.0.0.1` on a random port; checks `Host` on every request and
  `Origin` on every POST.
- The URL fragment carries a one-time capability, swapped on first use for an
  `HttpOnly; SameSite=Strict` session cookie. Reopening issues a fresh
  capability, with a fresh `?open=` query so an open tab loads it as a new
  page, and ends the previous page's session.
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

It exposes three tools that return "page opened" text and no URL.
`open_spotlight_review({case_dir})` opens the pending gate. The member says in
chat when they are done, and the agent resolves the case, which reads the
decision from disk. `open_spotlight_wiki_changes({case_dir})`, called after the
agent stages a wiki write, opens the same page on its Wiki changes view,
starting the server if none runs; with nothing staged it opens nothing. A
page already open for the case is left alone, so the member's session and
unsaved input survive; the agent asks them to reload it.
`open_spotlight_intake({case_dir})` opens the Material intake view the same
way (also `createIntakeOpener` in `open.mjs` for a harness), whether or not a
gate is pending, and leaves an open page alone: its "Material intake" button
leads there.

## Tests

```bash
npm test --prefix harness/review
python3 tests/review-gate-check.py
python3 tests/research-review-check.py
```
