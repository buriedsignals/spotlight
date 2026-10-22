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
| `SPOTLIGHT_ROUTES` | newsroom | JSON array of allowed model routes (see below). |
| `SPOTLIGHT_PYTHON` | no | Python interpreter for the Spotlight resolver (default `python3`). |

Keys a role needs are listed by name under that role's `keys` in `harness/composition.json`. A pi process receives only its own role's keys and its route's token; every other variable is dropped. Log lines and error messages never show those values.

### Routes and the newsroom edition

In the solo edition the model endpoint is the only route. It counts as run by the operator (`own`) when it is on the loopback interface, and as `third-party` otherwise.

In the newsroom edition, `SPOTLIGHT_ROUTES` lists every endpoint the harness may use:

```json
[
  {"name": "model", "base_url": "https://models.newsroom.example/v1", "model": "model-id", "inference": "own", "key_env": "NEWSROOM_MODEL_TOKEN"}
]
```

- `inference` is `own` for a route the newsroom runs itself and `third-party` otherwise.
- `key_env` names the variable that holds the route's token. The harness passes it to pi processes as `SPOTLIGHT_PROVIDER_API_KEY`.
- With no route, the harness does not start. `SPOTLIGHT_MODEL_BASE_URL`, `SPOTLIGHT_HELPER_URL` and `SPOTLIGHT_SEARCH_URL` must each be one of the routes or unset.
- Journalist input and local-corpus material go only to an `own` route. With no `own` route the harness refuses to process them. There is no fallback to another route.

## Tests

```bash
npm ci
npm test
```

The tests run the real launcher and real pi processes against a scripted OpenAI-compatible server on 127.0.0.1. They need no network and no model.
