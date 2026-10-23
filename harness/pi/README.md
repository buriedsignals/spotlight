# Spotlight Pi harness

Spotlight's local harness on [Pi](https://pi.dev) (`@earendil-works/pi-coding-agent`, pinned to an exact version in `package.json`). A chat client starts one long-lived launcher per chat:

```bash
node harness/pi/src/launcher.ts
```

The launcher runs `pi --mode rpc` for the orchestrator and passes stdin and stdout through untouched, so stdout carries only Pi's JSONL RPC records. The launcher, the extension and every pi process write diagnostics to stderr. Closing stdin shuts the chat down in order; SIGTERM is the fallback.

- **Processes.** The orchestrator, the investigator, the fact-checker and the analyst each run as their own `pi --mode rpc` process. Workers start on request from the orchestrator's `spawn_agent` tool, one per task, and cannot delegate. Every pi process starts with Pi's built-in tools, extension, skill, prompt-template and theme discovery, MCP and context files off. It loads only `src/extension.ts` and its role's skills by path, and declares an explicit tool list. Roles, skills, tools and keys come from `harness/composition.json`; worker personas come only from `agents/`.
- **Isolation.** Each pi process gets its own Pi configuration folder inside the harness data folder, so a personal Pi setup (settings, models, credentials, AGENTS.md) never applies.
- **Fact-checker isolation (D9).** A fact-checker task that names the research leads or clues, a lane folder under `research/swarm/`, the research triage or review files, or a loopback review page is refused before any process starts. Its own file tools and `exiftool` cannot reach that material either: `denyReads` in `harness/composition.json` hides `research/swarm/`, `data/leads.json`, `data/leads.jsonl`, `data/clues.json` and `data/research-*.json` from it.
- **State.** Case state lives on disk in the case folder and the Spotlight resolver. A closed chat or a killed process loses nothing that a new chat cannot resume.
- **One writer per case.** The launcher holds a lock file in the harness data folder with its PID and its children's PIDs. A second chat on a held case is refused. A lock left by a harness that died is taken over after its leftover pi processes are stopped. If the launcher itself is killed, its pi processes see their stdin close and shut down. On resume, a research lane folder whose `lane.json` does not show that it ended (missing, unreadable or still `running`) is discarded so the lane reruns.
- **No gate decisions from the model.** The transition tool accepts only `recordAttempt`, `requestFollowUp`, `sealInvestigationGate` and `decideIngest` with `completed`. Gate approvals, the report decision and the ingest request or decline come from the review page.
- **Provider privacy policy.** Fixed request-body fields are merged into every model request by a `before_provider_request` handler inside each pi process.
- **No shell.** Every step a skill would run through `execute-shell` is a typed tool or a check the harness runs itself (see [Tools](#tools)).

## Environment

Configuration comes only from the environment. A missing or invalid required variable stops the launcher with a plain message on stderr and exit code 2. A case held by another chat gives exit code 3.

| Variable | Required | Meaning |
|---|---|---|
| `SPOTLIGHT_MODEL_BASE_URL` | solo | OpenAI-compatible base URL of the model, for example `http://127.0.0.1:8080/v1`. In the newsroom edition, optional: it selects one of the routes. |
| `SPOTLIGHT_MODEL_ID` | solo | Model id sent to that endpoint. |
| `SPOTLIGHT_PROVIDER_API_KEY` | no | Key for the model endpoint. A local server without a key needs none. |
| `SPOTLIGHT_PROVIDER_REQUEST_BODY` | no | JSON object merged into every model request body, for example `{"provider":{"zdr":true}}`. Fixed fields win. |
| `SPOTLIGHT_MODEL_CONTEXT_WINDOW` | no | Context window in tokens (default 32768). |
| `SPOTLIGHT_MODEL_TIER` | no | `12b` (default), `26b`, `31b` or `frontier`. Sets where the conversation is folded (see [Conversation folding](#conversation-folding)). Any other value stops the launcher. |
| `SPOTLIGHT_COMPACT_AT`, `SPOTLIGHT_COMPACT_KEEP` | no | Override the local tier's fold point and the recent tokens kept verbatim. The fold point must be below the context window, and the kept tokens below the fold point. |
| `SPOTLIGHT_HELPER_URL` | no | Base URL of the helper model. |
| `SPOTLIGHT_SEARCH_URL` | no | Base URL of the search service. There is no default. |
| `SPOTLIGHT_CASES_ROOT` | yes | Absolute path of the cases folder. |
| `SPOTLIGHT_CASE_ID` | yes | Folder name of the case this chat works on. |
| `SPOTLIGHT_HARNESS_DATA` | yes | Absolute path of the harness data folder (locks, per-process Pi configuration). |
| `SPOTLIGHT_EDITION` | no | `solo` (default) or `newsroom`. |
| `SPOTLIGHT_ROUTES` | newsroom | JSON array of allowed model routes (see [Routes](#routes)). |
| `SPOTLIGHT_PYTHON` | no | Python interpreter for the Spotlight resolver and every Python seam the tools wrap (default `python3`). |
| `SPOTLIGHT_WORKER_TRANSCRIPT_DIR` | no | Debugging only: a folder where each worker's RPC records are written, one file per task. They contain case text; keep the folder private. |
| `SPOTLIGHT_CONFIG` | no | Absolute path of the installed `.spotlight-config.json` (default: the checkout root's). |
| `SPOTLIGHT_SENSITIVE` | no | `true` removes every tool that can reach the network from every process. |
| `SPOTLIGHT_ANONYMIZE_FETCH`, `TOR_SOCKS` | no | Tor posture, passed to the scraping seam. When on, `fetch` goes only through Tor and `http_get` and `browse` refuse. |
| `SPOTLIGHT_BROWSE_CHROMIUM`, `PLAYWRIGHT_BROWSERS_PATH` | no | The Chromium `browse` starts: an explicit executable, or where Playwright (and so `dev-browser install`) keeps its browsers. Default: the Chromium `dev-browser install` downloads. |
| `SPOTLIGHT_RLM_OPENAI_BASE_URL` | no | OpenAI-compatible base URL of the RLM, the small resident model that distills every `fetch` (see **Lead distillation** under [Tools](#tools)) and that `run_rlm` uses. It reads case material, so it must be an endpoint you run yourself: a loopback address in the solo edition, an `own` route in the newsroom edition. Anything else stops the launcher (exit code 2). |
| `SPOTLIGHT_RLM_OPENAI_MODEL`, `SPOTLIGHT_RLM_MODE`, `SPOTLIGHT_RLM_MODEL` | no | RLM model and `run_rlm` settings. |
| `SPOTLIGHT_RESEARCH_SWARMS` | no | `on` turns on the research swarm (Engine's "Research swarms" switch). Default `off`. |
| `OPENROUTER_API_KEY` | swarm | The member's key for the swarm route (solo). Never logged. |
| `SPOTLIGHT_SWARM_BASE_URL` | no | Swarm endpoint (default `https://openrouter.ai/api/v1`). In the newsroom edition it must be one of `SPOTLIGHT_ROUTES`, whose `key_env` is then the key. |
| `SPOTLIGHT_SWARM_MODEL`, `SPOTLIGHT_SWARM_FALLBACK_MODEL` | no | Swarm model (default `deepseek/deepseek-v4-flash`) and fallback (default `z-ai/glm-5.3`; empty for none). |
| `SPOTLIGHT_SWARM_REQUEST_BODY` | no | JSON merged into every swarm request; `provider.zdr` is always `true`. |
| `SPOTLIGHT_SWARM_CONCURRENCY` | no | Swarm lanes running at once (default 8). |

Keys a role needs are listed by name under that role's `keys` in `harness/composition.json`. A pi process receives only its own role's keys and its route's token; every other variable is dropped. Log lines and error messages never show those values.

### Routes

Every process reads the case, which may hold journalist input or local-corpus content, so every process runs only on a route the operator runs itself (`own`). With none, the launcher stops before starting any pi process (exit code 2). There is no fallback to another route.

- **Solo:** the model endpoint is the only route. It is `own` when its host is `localhost`, `[::1]` or an IPv4 address in 127.0.0.0/8 (`127.example.com` is not).
- **Newsroom:** `SPOTLIGHT_ROUTES` lists every endpoint the harness may use. `inference` is `own` for a route the newsroom runs or contracts itself, `third-party` otherwise; `key_env` names the variable holding the route's token. `SPOTLIGHT_MODEL_BASE_URL`, `SPOTLIGHT_HELPER_URL` and `SPOTLIGHT_SEARCH_URL` must each be one of the routes or unset.

```json
[{"name": "model", "base_url": "https://models.newsroom.example/v1", "model": "model-id", "inference": "own", "key_env": "NEWSROOM_MODEL_TOKEN"}]
```

## Research lanes

A case whose approved methodology has a `research_plan` goes through Research before Investigation. Code runs it, never a model's decision: when the orchestrator's `spotlight_resolve` reports the Research phase, it asks the launcher, which checks the phase itself, reads the approved plan and runs each topic brief as one lane (`src/lanes.ts`). The resolution comes back with the round as `research_round`.

- **Three routes.** Every pi process runs on the pipeline route, held to a route the operator runs (`routeFor`, see [Routes](#routes)). The third route, `swarm`, is the research swarm: a cloud endpoint (OpenRouter by default) that may be third-party because it receives only an approved topic brief and what that lane fetched from the public web. It exists only when `SPOTLIGHT_RESEARCH_SWARMS=on` and a key is set; never in sensitive mode; in the newsroom edition only on an allowed route. Swarm lanes are not pi processes, so the extension's refusal to run case-reading processes on a third-party route stays as it is.
- **Lane types** are `lanes/*.yaml` (web, registries, archives, social, technical, news-courts, local-corpus, prior-knowledge, ad-hoc). A swarm type may name only `search`, `fetch` and `http_get`; a local type only `list_case_files`, `read_case_file` (the case's `research/` and `evidence/`, not other lanes) and `query_vault`. A lane gets a key only when its type declares it and the plan grants it to that topic; `ad-hoc` gets none. Lanes reuse the typed tools, so egress, the guarded proxy and Tor mode apply unchanged; fetched text reaches the model only between source markers, as data.
- **What a swarm request may carry.** The persona (`agents/researcher.md`), the brief (`topic_id`, `direction_id`, `lane_type`, questions, seed queries, sources to try, Anomaly methods), and the lane's own turns and tool results. A topic carrying any other field, a local or journalist-input topic routed to the swarm, or a brief not from the plan is refused, and the check runs again before every request. Requests carry `provider.zdr: true` and the key only in the `Authorization` header.
- **Local lanes.** `local-corpus` and `prior-knowledge` run on the operator's model, one at a time. Leads from a topic marked `journalist_input` (granted in the plan's consent) carry `journalist_input: true`. In the newsroom edition the prior-knowledge lane is left out until `search_knowledge` is available here.
- **Budget, not a count.** Swarm lanes start, up to `SPOTLIGHT_SWARM_CONCURRENCY` at once, while the round budget remains (`round_budget_usd`, capped by `case_budget_usd` and $5 per case across rounds, with OpenRouter's reported `usage.cost`; an answer with no cost is priced high on purpose). Once it is spent no new topic starts, running ones finish, and the topics left out are recorded. Each lane also stops at its `token_budget` (default 200,000), 40 turns or 60 tool calls, as `partial`.
- **Leads.** The lane records leads with `record_lead` (a captured `source_id`, summary, subjects, excerpt, optional attribution and `facts`) and `record_absence`. Code fills `lead_id`, `capture`, `content_ref`, `content_sha256` and `provenance`; a lead from a search result it did not fetch is a `snippet`, with no evidence record. Each lead is judged at once by D3, the research checks' own `d3_excerpt` (`scripts/research_checks.py`): the excerpt must be verbatim in a stored source whose sha256 matches, so a snippet fails until the lane fetches the page. A failing lead is returned to the lane once and stays `unverifiable` if the revision fails too. A fact is kept only when its value is verbatim in the excerpt. When a lane ends, D2 (`read_lane`) checks its files; an invalid lane is failed.
- **Failures.** A rejected key (401), no credit (402), or errors that outlast three retries (408, 429, 500, 502-504, or no answer) stop the round: running swarm lanes stop, nothing falls back to the local model, and the round's `stopped` says what failed and how to fix it, without the key. A stopped round reruns only when the member asks (`retry_research`), and then only the topics without a finished lane. The plan's `subscription_subagents` mode cannot run here and stops with that reason.
- **Files.** Each lane writes only `research/swarm/<topic-id>/` (`lane.json`, `leads.jsonl`, `sources/`), refusing a symlinked or non-folder path. Rounds are recorded in `research/rounds.json` (round, plan hash, mode, budget, spend, what ran, left out, refused, stopped). A topic left out or refused still gets a `lane.json` (`not_started` or `failed`, with the reason). Once every approved topic has ended, the harness runs `scripts/research-merge.py` itself (there is no shell for the model to run it) and the round's `merge` reports the outcome; `data/leads.json` and `data/clues.json` then move the case to the Research gate. A stopped round is not merged.

## Conversation folding

A small local model degrades as its context grows: the conversation alone (reasoning, delegations, tool results) can stall it after a single gate. The case's state lives in the case folder and the resolver, not in the transcript, so on the local tiers the harness folds the conversation into a checkpoint once it passes the tier's fold point.

| Tier | Fold point (tokens) | Kept verbatim (tokens) |
|---|---|---|
| `12b` | 16384 | 4000 |
| `26b` | 24576 | 6000 |
| `31b` | 28672 | 7000 |
| `frontier` | Pi's default (context window - 16384) | Pi's default (20000) |

- **Mechanism.** Pi's own compaction. Before every pi process starts, the launcher writes `settings.json` in that process's Pi configuration folder with `compaction.reserveTokens` = context window - fold point and `compaction.keepRecentTokens` = the kept tokens. Pi compacts when the estimated context passes context window - `reserveTokens`, between turns and before a new prompt. `--no-session` keeps the session in memory and does not turn compaction off. The frontier tier's file holds no compaction settings, so Pi's defaults apply. The same settings apply to the orchestrator and to every worker.
- **Checkpoint.** On the local tiers the extension's `session_before_compact` handler replaces Pi's summary with a checkpoint in five sections: Goal, Progress, Decisions, Next, Open gate. The session model writes it, on the same route as every other request. The RLM endpoint is not used: it is not one of the routes the launcher checks, and sensitive mode removes it. The checkpoint ends with a note that the case files and `spotlight_resolve` are the source of truth. If the checkpoint request fails, the compaction is skipped and Pi tries again at its next check.
- **Privacy policy.** Pi sends a summary request outside the agent loop, where `before_provider_request` does not run. The extension therefore applies the fixed request-body fields to every summary request itself, on every tier; a request the policy cannot apply to is discarded and the compaction is skipped.

## Tools

There is no shell in this harness. Each tool takes typed arguments, refuses anything outside its schema, and starts its backing program (if any) as a plain argument list with the shell off. Python seams run with `SPOTLIGHT_PYTHON` from the checkout root. Each role's tool list is its `tools` allowlist in `harness/composition.json`.

| Tool | Backing | Roles |
|---|---|---|
| `read_file`, `write_file`, `list_files`, `grep_files` | the case folder | orchestrator, investigator, fact-checker; analyst, narrowed (see **Case files**) |
| `edit_file` | the case folder | investigator |
| `hash_files` | the case folder | investigator, fact-checker |
| `fetch` | `python -m integrations.scraping` (Crawl4AI; Firecrawl fallback with `FIRECRAWL_API_KEY`), then the RLM when one is configured | investigator, fact-checker |
| `search` | `python -m integrations.search` (SearXNG at `SPOTLIGHT_SEARCH_URL`) | investigator, fact-checker |
| `http_get` | one GET from the harness itself | investigator, fact-checker |
| `browse` | the dev-browser script runner (`dev-browser --connect <endpoint> run`) against a headless Chromium the harness starts behind the egress proxy, with a script the harness writes | investigator, fact-checker |
| `query_vault` | `scripts/query_vault.py` | investigator, fact-checker, analyst |
| `osint_tools` | `scripts/osint-tools.py find` and `categories` | orchestrator, investigator, fact-checker |
| `navigator_tools` | `navigator tools find` and `show` | investigator, fact-checker |
| `exiftool` | `exiftool -json` | investigator, fact-checker |
| `decision_signals` | `scripts/decision-signals.py` | orchestrator |
| `provenance_manifest` | `scripts/build-provenance-manifest.py` (build, check, sign) | orchestrator |
| `build_review_html` | `scripts/build-review-html.py --stdout`, written to `review.html` under the case-file rules | orchestrator |
| `run_rlm` | `integrations/rlm/run_rlm.py` | orchestrator |
| `integration_status` | `integrations/preflight.py --json` | orchestrator |

`browse` is backed by dev-browser. `integrations/browse` holds only a manifest for the optional browse.sh CLI and backs nothing here. The model gives `browse` a URL, never a script, so the browser opens exactly one guarded page and returns its text, its HTML or a screenshot saved in the case.

**Evidence locators and hashes.** The validators re-select every `source_ref` and recompute every declared SHA-256 (`scripts/evidence_anchors.py`). `hash_files` returns the SHA-256 of a case file's bytes on disk, the digest the validators recompute for `sha256`, `anchor_sha256` and `original_artifact_sha256`, for any artifact an agent wrote or derived itself. `read_file` with `offset`/`limit`, and `grep_files` line numbers, count lines as the validators do (Python's universal newlines and `splitlines()`), so they serve as `line_start`/`line_end`. `read_file` with `json_pointer` returns the text the validators select for a `source_ref` `json_pointer`: a string value as is, anything else as `json.dumps(..., ensure_ascii=False, sort_keys=True)`. Limit: a non-integer number, or an integer beyond 2^53, can print differently from Python's; anchor quotes to string values.

**Lead distillation.** A raw scraped page runs to tens of thousands of tokens, which a small local model cannot hold. Every pi process runs on a route the operator runs (see [Routes](#routes)), so the local tier is simply a harness started with `SPOTLIGHT_RLM_OPENAI_BASE_URL`; the launcher passes that endpoint to its processes only after confirming it is one the operator runs, and `fetch` refuses an endpoint without that confirmation. On this tier `fetch` needs `save_as`. Once the final URL has passed the egress guard, `fetch` writes the page as fetched to `<save_as>.raw`, sends it to the RLM (the request in `integrations/scraping/rlm-leads.json`, which the scraping seam's `--rlm` path also uses), writes the leads to `save_as` under a `# RLM-distilled leads from <url>` header, and returns the leads, both paths and the SHA-256 of both files. The page itself never comes back to the agent. If the RLM fails, the page stays at `<save_as>.raw`, no lead file is written, and the result says why, again without the page. The validators refuse a lead file as a `source_ref` and name its `.raw`; the `.raw` anchors like any case file. Without `SPOTLIGHT_RLM_OPENAI_BASE_URL`, `fetch` returns and saves the page as before, with `distilled: false` and the reason in the result. Sensitive mode removes `fetch`, so nothing is distilled there.

**Case files.** Reads stay inside the case folder (plus the checkout's `schemas/`); a symlink that leads out is refused. Writes stay inside the caller's write folder (the case folder for these roles), never follow a symlink, and never touch `data/orchestration.json`, `data/review-nonces.json`, `data/*-review.json` or any file whose name contains `receipt`. The check ignores letter case. A role with `reads` or `writes` in `harness/composition.json` is narrowed further: its file tools reach only those case files (reads through a symlink are checked where they lead), and such a role may declare only the file tools, `invoke_skill` and `query_vault`, or the launcher refuses to start. The analyst reads the approved findings, the fact-check, the passages, the source positions, the evidence bundle, the decision signals and its own proposal, and writes only `data/knowledge-proposal.json`. A role with `denyReads` keeps its other tools and reads the rest of the case, but its file tools and `exiftool` refuse, leave out of listings and skip in searches every matching case file (an entry ending in `/` is a folder, `*` matches within one path segment; reads through a symlink are checked where they lead).

**Egress.** `fetch`, `browse` and `http_get` accept only http(s) URLs without credentials whose addresses are public. Loopback, private, link-local (including the 169.254.169.254 metadata address), carrier-grade NAT, multicast, reserved and documentation ranges, IPv4-mapped and NAT64 forms, single-label names, and `localhost`, `.local`, `.internal`, `.lan` and `.home.arpa` names are refused. A hostname is resolved inside the connection's own lookup, so the checked address is the connected one, and every redirect hop is checked again. `http_get` does its request entirely under this guard. `fetch` and `browse` first walk the redirect chain under the guard and hand their backing only the final URL; the URL the backing reports ending on is checked again before anything is returned. `fetch` and `browse` also start a forward proxy on a loopback port for each call (`src/tools/proxy.ts`). `fetch` passes it to the scraping seam as `SPOTLIGHT_BROWSER_PROXY`, and Crawl4AI's Chromium runs behind it. `browse` starts its own headless Chromium behind it, with a fresh profile and DevTools on an ephemeral loopback port, runs its script with `dev-browser --connect` to that browser, and stops the browser and the proxy when the call ends (dev-browser takes no proxy or launch flags for the browsers it launches itself). Both browsers run with `--proxy-bypass-list=<-loopback>`, so loopback requests are proxied too. Every connection the browser makes (subresources, scripts' `fetch()`, redirects, WebSockets, which Chromium tunnels with CONNECT) passes the same guard, and a hostname is resolved in the proxy's own connection, so a name that later resolves to a private address is refused. The proxy follows no redirects itself. Inside the browser, `browse` also holds every request whose host is an IPv4 address in the same non-public table, an IPv6 literal, a single-label name or one of those local names, as defence in depth. `browse` uses `SPOTLIGHT_BROWSE_CHROMIUM` when it is set, otherwise the Chromium that `dev-browser install` downloads (located through dev-browser's own playwright-core under `~/.dev-browser`), and refuses with an error naming both when neither exists. Limits: neither browser's WebRTC (UDP) traffic is covered. `osint_tools` takes no URL and reads only the local index. `search` sends the query to the operator's search service.

**Tor mode** (`SPOTLIGHT_ANONYMIZE_FETCH` set to `1`, `true`, `yes` or `on`, read as the scraping seam reads it). `fetch` makes no connection and no DNS query of its own: it checks the URL without the network (scheme, credentials, literal addresses, local names), passes it to the seam with `--tor` (no egress proxy: the browser sends everything, loopback requests included, to Tor's SOCKS port rather than to the local machine), and checks the URL the seam reports ending on the same way. `http_get` and `browse` cannot route through Tor, so they refuse.

**Sensitive mode** (`SPOTLIGHT_SENSITIVE=true`, or `sensitive: true` in the frontmatter of `AGENTS.md`) removes `fetch`, `search`, `http_get`, `browse`, `navigator_tools`, `run_rlm` (it can send case text to `SPOTLIGHT_RLM_OPENAI_BASE_URL`) and `integration_status` (the integration preflight can probe endpoints) from every process. `decision_signals` and `provenance_manifest` stay: they receive `SPOTLIGHT_SENSITIVE` and refuse their remote step themselves.

**Checks at fixed points.** When a worker finishes, the launcher resolves the case phase and runs the validators for what that worker writes, then appends their outcome to the worker's answer: after the investigator while the case is in Brief, Methodology or awaiting methodology approval, `validate-methodology-navigator.py`; after the investigator in Investigation, `validate-case.py`; after the fact-checker, `validate-case.py` and `validate-fact-check.py`; after the analyst, `build-knowledge-batch.py {CASE_DIR} --review --model investigator=<id> --model fact-checker=<id>`, with the model id of the harness's route (every process runs on it). The analyst starts only at Ingest and only in the solo edition (`phases` and `editions` in `harness/composition.json`); any other `spawn_agent` for it is refused. A findings list may be empty only with an explicit `no_qualifying_findings: {"reason": ...}` record.

### Shell steps and their replacements

Every `execute-shell` step in `agents/` and in the skills these roles load, and where it went:

| Where | Shell step | In this harness |
|---|---|---|
| investigator, fact-checker, content-access, web-archiving, integrations | `curl` GET to registries, archives and APIs (WHOIS, Unpaywall, CORE, Semantic Scholar, OpenAlex, Wayback CDX, availability and save, archive.today lookup) | `http_get` |
| content-access, social-media-intelligence, technical-investigation | `curl --get --data-urlencode` query parameters (Unpaywall, CORE, Semantic Scholar, OpenAlex, Wayback CDX, GitHub API) | `http_get` with `query` (values percent-encoded and appended to the URL) |
| web-archiving | `curl -X POST` to archive.today's submit form | not available (GET only); the step reports that it needs a POST |
| content-access, web-archiving, dev-browser capture | `spotlight_safe.py validate-url`, `validate-doi`, `validate-timestamp` | not needed: tool schemas and the egress guard check arguments |
| web-archiving, dev-browser capture, investigator, fact-checker | `spotlight_safe.py sha256`, or a hash of a derived artifact (extracted text, saved API JSON, transcript) | `hash_files`; `browse`, `fetch` and `http_get` also return the SHA-256 of what they download |
| investigator, fact-checker | `grep -n`, `sed -n` or Python to find a line range or resolve a JSON Pointer for a `source_ref` | `grep_files` (line numbers), `read_file` with `offset`/`limit` or `json_pointer` |
| technical-investigation (`references/github-public.md`; no step names a command) | `git clone` and `git merge-base` of a public repository, as an agent with a shell may improvise | not available: no git in this harness; the step reports that it needs a clone. The repository's public web API is reachable through `http_get` |
| editorial-review | building `review.html` (payload serialization and template injection) | `build_review_html` (`scripts/build-review-html.py`) |
| web-archiving | `scripts/spotlight-python -m integrations.scraping {URL} --out ...` | `fetch` with `save_as` |
| dev-browser integration, web-archiving dev-browser capture | `dev-browser run <script>`, then `mkdir`, `cp`, `rm` of the capture | `browse` (text, HTML or screenshot saved with `save_as`) |
| fact-checker, technical-investigation | `exiftool {file}` | `exiftool` |
| phase-methodology, osint, integrations | `scripts/osint-tools.py find` | `osint_tools` |
| phase-preflight, phase-methodology | `scripts/osint-tools.py categories` | `osint_tools` |
| phase-preflight | `uv run ... scripts/osint-tools.py build` | not available: an install step that downloads a dataset; `osint_tools` reports a missing index |
| phase-methodology, osint, navigator, investigator | `navigator tools find` and `show` | `navigator_tools` |
| phase-methodology | `scripts/validate-methodology-navigator.py` | harness check after the investigator while the case is in Brief, Methodology or awaiting methodology approval |
| phase-methodology | `integrations/rlm/run_rlm.py` | `run_rlm` |
| phase-investigation | `scripts/validate-case.py` after the investigator | harness check |
| phase-investigation | `scripts/validate-case.py` and `scripts/validate-fact-check.py` after the fact-checker | harness check |
| phase-investigation | `scripts/migrate-source-expressions.py` (dry run and apply) | not available: an operator-reviewed flow outside the model |
| phase-investigation-gate, report-drafting, ingest | `scripts/decision-signals.py` (including `--check` and `--case-opt-in`) | `decision_signals` |
| phase-investigation-gate, provenance-signing | `scripts/build-provenance-manifest.py` (build, `--check-current`, `--sign-endpoint`) | `provenance_manifest` |
| phase-preflight | step 0: `test -f skills/... && test -f scripts/...` | skipped: `spotlight_resolve` answering confirms the checkout |
| phase-preflight | `scripts/spotlight-orchestration.py status` | `spotlight_resolve` |
| phase-preflight, integrations | `integrations/preflight.py --json` | `integration_status` |
| phase-preflight | Crawl4AI probe (`integrations.scraping https://example.com`) and SearXNG `curl` probe | not run by the orchestrator, which has no web tools; a worker's first `fetch` or `search` reports a broken backing |
| phase-preflight, social-media-intelligence | `integrations.apify status` and `run`, `apify call` | not available |
| phase-preflight | `test -f ~/.mycroft/monitoring/monitor.py` | not available (monitoring runs outside this harness) |
| phase-ingest | the analyst proposal, then `build-knowledge-batch.py --review` | `spawn_agent` with `analyst`; the harness runs the build when it finishes |
| ingest | `spotlight_safe.py destructive-probe` and `resolve-path`, `rm` of the ingest lock, `ingest-eligibility.py`, `ingest-source-expressions.py`, `validate-install-config.py` | not available: Ingest needs `vault-write`, which this harness does not offer yet |

## Tests

```bash
npm ci
npm test
```

The tests run the real launcher and real pi processes against a scripted OpenAI-compatible server on 127.0.0.1. They need no network and no model.
