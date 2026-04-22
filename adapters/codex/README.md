# Codex CLI adapter

Runtime adapter for OpenAI's Codex CLI (`@openai/codex`). Tested against v0.122.0.

See `docs/runtimes.md#codex-cli` for the verb-by-verb binding table and sub-agent pattern.

## Install

```bash
npm install -g @openai/codex
codex login
```

## Configure

Copy the example config and adapt to your setup:

```bash
cp adapters/codex/config.toml.example ~/.codex/config.toml
```

The example defines three profiles:

- `orchestrator` — runs the Spotlight top-level skill (Phase 0 → Gate 1)
- `investigator` — spawned by the orchestrator as a subprocess for PLANNING and EXECUTION
- `fact-checker` — spawned after each investigator EXECUTION cycle

Adjust `model` and `max_output_tokens` per profile. Costs and rate limits vary by plan — see `docs/runtimes.md#known-limitations-v0122`.

## Run an investigation

From the repo root:

```bash
codex exec \
  --profile orchestrator \
  --skip-git-repo-check \
  "Invoke the spotlight skill and start a new investigation. Brief: <your brief here>."
```

### In Docker (recommended for isolation)

Codex's built-in bubblewrap sandbox does not start inside unprivileged containers. Add the bypass flag — the container itself is already your sandbox:

```bash
docker run --rm -it \
  -v "$(pwd):/workspace" \
  -v "$HOME/.codex:/root/.codex" \
  -w /workspace \
  node:20-slim bash -lc "npm i -g @openai/codex && codex exec \
    --profile orchestrator \
    --skip-git-repo-check \
    --dangerously-bypass-approvals-and-sandbox \
    'Invoke the spotlight skill …'"
```

## Sub-agent pattern

The orchestrator skill calls `spawn-agent(agent_id, prompt, config)` as pseudo-code. Under Codex, this resolves to a shell call — the orchestrator uses `execute-shell` to launch a nested `codex exec`:

```bash
codex exec \
  --ephemeral \
  --skip-git-repo-check \
  --dangerously-bypass-approvals-and-sandbox \
  --profile fact-checker \
  --output-last-message cases/{project}/data/fact-check.stdout \
  "$(cat <<'EOF'
MODE: VERIFY
PROJECT: {project}
VAULT_PATH: {vault}
CYCLE: {cycle}

$(cat agents/fact-checker.md)
EOF
)"
```

The sub-agent writes its structured output to `cases/{project}/data/fact-check.json` per the schema in `schemas/fact-check.schema.json`. The orchestrator reads that file after the subprocess exits — **contract is file-based, not stdout**.

`--ephemeral` keeps the sub-agent session off disk (no `.codex/sessions/` pollution). Each sub-agent call is a fresh context — the isolation Spotlight requires for independent verification.

## Sensitive mode / local inference

Codex 0.122 has a native `--oss` flag that detects Ollama or LM Studio on `127.0.0.1:11434` / `:1234`. Use it rather than a custom `model_providers` entry in `config.toml` — Codex 0.122 deprecated `wire_api = "chat"` and requires `"responses"`, which Ollama/llama-server do not speak yet ([codex#7782](https://github.com/openai/codex/discussions/7782)).

```bash
SPOTLIGHT_SENSITIVE=true codex exec \
  --oss \
  --local-provider ollama \
  --model gemma-4-26B-A4B-it \
  --skip-git-repo-check \
  "<prompt>"
```

Then, for defence-in-depth, wrap `firecrawl` in a shell alias that refuses calls when `SPOTLIGHT_SENSITIVE=true` — otherwise the orchestrator can still hit the network through `execute-shell`.

See `docs/runtimes.md#sensitive-mode-across-runtimes` for the cross-runtime contract.

### Local inference in Docker

When Ollama runs in a separate container, `--oss` fails because Codex hardcodes `127.0.0.1:11434`. Share the Ollama container's network namespace so Codex sees it as localhost:

```bash
docker run --rm -it \
  --network container:ollama-spotlight \
  -v "$(pwd):/workspace" \
  -w /workspace \
  codex-image \
  codex exec --oss --local-provider ollama --model <model> \
    --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox "<prompt>"
```

### Model size reality check

Per `AGENTS.md`, Spotlight's operator model is Gemma 4 26B A4B (Q4_K_M, ~18 GB). Smaller tool-capable models (e.g. `llama3.2:3b`) technically run but **do not reason well enough to invoke tools autonomously** — they will answer "I don't see the file" rather than calling `read_file`. Investigation quality collapses. Always target a 26B+ class model in production.

## Known gotchas

- **Bubblewrap in Docker** — always pass `--dangerously-bypass-approvals-and-sandbox` when containerised. Without it, `execute-shell` fails with `bwrap: No permissions to create a new namespace`.
- **OAuth callback in Docker** — Codex's `codex login` starts a local web server on `127.0.0.1:1455` inside the container. macOS Docker Desktop does not forward that to the host browser. Workaround: copy the callback URL from your browser (after clicking "Authorize") and `curl` it from inside the container.
- **Rate limits on free tier** — a full investigation needs ~100+ turns. ChatGPT free login caps out long before. Use Plus/Pro or `OPENAI_API_KEY`.
- **Shared auth across sub-agents** — the OAuth token is shared between orchestrator and spawned agents. Rate limits apply to the sum.
- **`wire_api = "chat"` deprecated** — do not add an Ollama/llama-server entry under `[model_providers.*]` in `config.toml`; Codex 0.122 rejects it. Use `--oss` instead.
- **Docker VM memory** — Docker Desktop on macOS caps container RAM at the VM size (default ~8 GB). Gemma 4 26B A4B needs ~18 GB — bump the VM to 24 GB in Preferences → Resources → Memory before pulling.
