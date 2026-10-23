# Spotlight Pi harness

Spotlight's local harness on [Pi](https://pi.dev) (`@earendil-works/pi-coding-agent`, pinned to an exact version in `package.json`). A chat client starts one long-lived launcher per chat:

```bash
node harness/pi/src/launcher.ts
```

The launcher runs `pi --mode rpc` for the orchestrator and passes stdin and stdout through untouched, so stdout carries only Pi's JSONL RPC records. The launcher, the extension and every pi process write diagnostics to stderr. Closing stdin shuts the chat down in order; SIGTERM is the fallback.

- **Processes.** The orchestrator, the investigator and the fact-checker each run as their own `pi --mode rpc` process. Workers start on request from the orchestrator's `spawn_agent` tool, one per task, and cannot delegate. Every pi process starts with Pi's built-in tools, extension, skill, prompt-template and theme discovery, MCP and context files off. It loads only `src/extension.ts` and its role's skills by path, and declares an explicit tool list. Roles, skills, tools and keys come from `harness/composition.json`; worker personas come only from `agents/`.
- **Isolation.** Each pi process gets its own Pi configuration folder inside the harness data folder, so a personal Pi setup (settings, models, credentials, AGENTS.md) never applies.
- **State.** Case state lives on disk in the case folder and the Spotlight resolver. A closed chat or a killed process loses nothing that a new chat cannot resume.
- **One writer per case.** The launcher holds a lock file in the harness data folder with its PID and its children's PIDs. A second chat on a held case is refused. A lock left by a harness that died is taken over after its leftover pi processes are stopped. If the launcher itself is killed, its pi processes see their stdin close and shut down. On resume, a research lane folder without a completed `lane.json` is discarded so the lane reruns.
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
| `SPOTLIGHT_RLM_*` | no | RLM endpoint settings for `run_rlm`. |

Keys a role needs are listed by name under that role's `keys` in `harness/composition.json`. A pi process receives only its own role's keys and its route's token; every other variable is dropped. Log lines and error messages never show those values.

### Routes

Every process reads the case, which may hold journalist input or local-corpus content, so every process runs only on a route the operator runs itself (`own`). With none, the launcher stops before starting any pi process (exit code 2). There is no fallback to another route.

- **Solo:** the model endpoint is the only route. It is `own` when its host is `localhost`, `[::1]` or an IPv4 address in 127.0.0.0/8 (`127.example.com` is not).
- **Newsroom:** `SPOTLIGHT_ROUTES` lists every endpoint the harness may use. `inference` is `own` for a route the newsroom runs or contracts itself, `third-party` otherwise; `key_env` names the variable holding the route's token. `SPOTLIGHT_MODEL_BASE_URL`, `SPOTLIGHT_HELPER_URL` and `SPOTLIGHT_SEARCH_URL` must each be one of the routes or unset.

```json
[{"name": "model", "base_url": "https://models.newsroom.example/v1", "model": "model-id", "inference": "own", "key_env": "NEWSROOM_MODEL_TOKEN"}]
```

## Tools

There is no shell in this harness. Each tool takes typed arguments, refuses anything outside its schema, and starts its backing program (if any) as a plain argument list with the shell off. Python seams run with `SPOTLIGHT_PYTHON` from the checkout root. Each role's tool list is its `tools` allowlist in `harness/composition.json`.

| Tool | Backing | Roles |
|---|---|---|
| `read_file`, `write_file`, `list_files`, `grep_files` | the case folder | orchestrator, investigator, fact-checker |
| `edit_file` | the case folder | investigator |
| `hash_files` | the case folder | investigator, fact-checker |
| `fetch` | `python -m integrations.scraping` (Crawl4AI; Firecrawl fallback with `FIRECRAWL_API_KEY`) | investigator, fact-checker |
| `search` | `python -m integrations.search` (SearXNG at `SPOTLIGHT_SEARCH_URL`) | investigator, fact-checker |
| `http_get` | one GET from the harness itself | investigator, fact-checker |
| `browse` | the dev-browser script runner (`dev-browser --connect <endpoint> run`) against a headless Chromium the harness starts behind the egress proxy, with a script the harness writes | investigator, fact-checker |
| `query_vault` | `scripts/query_vault.py` | investigator, fact-checker |
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

**Case files.** Reads stay inside the case folder (plus the checkout's `schemas/`); a symlink that leads out is refused. Writes stay inside the caller's write folder (the case folder for these roles), never follow a symlink, and never touch `data/orchestration.json`, `data/review-nonces.json`, `data/*-review.json` or any file whose name contains `receipt`. The check ignores letter case.

**Egress.** `fetch`, `browse` and `http_get` accept only http(s) URLs without credentials whose addresses are public. Loopback, private, link-local (including the 169.254.169.254 metadata address), carrier-grade NAT, multicast, reserved and documentation ranges, IPv4-mapped and NAT64 forms, single-label names, and `localhost`, `.local`, `.internal`, `.lan` and `.home.arpa` names are refused. A hostname is resolved inside the connection's own lookup, so the checked address is the connected one, and every redirect hop is checked again. `http_get` does its request entirely under this guard. `fetch` and `browse` first walk the redirect chain under the guard and hand their backing only the final URL; the URL the backing reports ending on is checked again before anything is returned. `fetch` and `browse` also start a forward proxy on a loopback port for each call (`src/tools/proxy.ts`). `fetch` passes it to the scraping seam as `SPOTLIGHT_BROWSER_PROXY`, and Crawl4AI's Chromium runs behind it. `browse` starts its own headless Chromium behind it, with a fresh profile and DevTools on an ephemeral loopback port, runs its script with `dev-browser --connect` to that browser, and stops the browser and the proxy when the call ends (dev-browser takes no proxy or launch flags for the browsers it launches itself). Both browsers run with `--proxy-bypass-list=<-loopback>`, so loopback requests are proxied too. Every connection the browser makes (subresources, scripts' `fetch()`, redirects, WebSockets, which Chromium tunnels with CONNECT) passes the same guard, and a hostname is resolved in the proxy's own connection, so a name that later resolves to a private address is refused. The proxy follows no redirects itself. Inside the browser, `browse` also holds every request whose host is an IPv4 address in the same non-public table, an IPv6 literal, a single-label name or one of those local names, as defence in depth. `browse` uses `SPOTLIGHT_BROWSE_CHROMIUM` when it is set, otherwise the Chromium that `dev-browser install` downloads (located through dev-browser's own playwright-core under `~/.dev-browser`), and refuses with an error naming both when neither exists. Limits: neither browser's WebRTC (UDP) traffic is covered. `osint_tools` takes no URL and reads only the local index. `search` sends the query to the operator's search service.

**Tor mode** (`SPOTLIGHT_ANONYMIZE_FETCH` set to `1`, `true`, `yes` or `on`, read as the scraping seam reads it). `fetch` makes no connection and no DNS query of its own: it checks the URL without the network (scheme, credentials, literal addresses, local names), passes it to the seam with `--tor` (no egress proxy: the browser sends everything, loopback requests included, to Tor's SOCKS port rather than to the local machine), and checks the URL the seam reports ending on the same way. `http_get` and `browse` cannot route through Tor, so they refuse.

**Sensitive mode** (`SPOTLIGHT_SENSITIVE=true`, or `sensitive: true` in the frontmatter of `AGENTS.md`) removes `fetch`, `search`, `http_get`, `browse`, `navigator_tools`, `run_rlm` (it can send case text to `SPOTLIGHT_RLM_OPENAI_BASE_URL`) and `integration_status` (the integration preflight can probe endpoints) from every process. `decision_signals` and `provenance_manifest` stay: they receive `SPOTLIGHT_SENSITIVE` and refuse their remote step themselves.

**Checks at fixed points.** When a worker finishes, the launcher resolves the case phase and runs the validators for what that worker writes, then appends their outcome to the worker's answer: after the investigator while the case is in Brief, Methodology or awaiting methodology approval, `validate-methodology-navigator.py`; after the investigator in Investigation, `validate-case.py`; after the fact-checker, `validate-case.py` and `validate-fact-check.py`. A findings list may be empty only with an explicit `no_qualifying_findings: {"reason": ...}` record.

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
| ingest | `spotlight_safe.py destructive-probe` and `resolve-path`, `rm` of the ingest lock, `ingest-eligibility.py`, `ingest-source-expressions.py`, `validate-install-config.py` | not available: Ingest needs `vault-write`, which this harness does not offer yet |

## Tests

```bash
npm ci
npm test
```

The tests run the real launcher and real pi processes against a scripted OpenAI-compatible server on 127.0.0.1. They need no network and no model.
