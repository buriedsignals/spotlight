# Codex CLI adapter

Runtime adapter for OpenAI's Codex CLI (`@openai/codex`, reviewed setup pin `0.138.0`).

See `docs/runtimes.md#codex-cli` for the verb-by-verb binding table and sub-agent pattern.

## Install

```bash
npm install -g @openai/codex@0.138.0
codex login
```

## Configure

Copy the example config and adapt to your setup:

```bash
cp adapters/codex/config.toml.example ~/.codex/config.toml
```

The example defines three profiles:

- `orchestrator` — runs the Spotlight top-level skill (Phase 0 → Investigation gate)
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

## Sub-agent pattern

The orchestrator skill calls `spawn-agent(agent_id, prompt, config)` as pseudo-code. Under Codex, this resolves to a shell call — the orchestrator uses `execute-shell` to launch a nested `codex exec`:

```bash
codex exec \
  --ephemeral \
  --skip-git-repo-check \
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

Codex 0.122 has a native `--oss` flag that detects Ollama on `127.0.0.1:11434`. Use it rather than a custom `model_providers` entry in `config.toml` — Codex 0.122 deprecated `wire_api = "chat"` and requires `"responses"`, which Ollama/llama-server do not speak yet ([codex#7782](https://github.com/openai/codex/discussions/7782)).

Local RLM uses the same localhost boundary. If `python3 integrations/rlm/run_rlm.py …`
reports `gemma4:e4b` unavailable from inside Codex, first rerun the check with
host-approved localhost access. A sandboxed `127.0.0.1:11434` failure is an
adapter permission problem, not evidence that Ollama or the model is absent.

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

### Model size reality check

Per `AGENTS.md`, Spotlight's operator model is Gemma 4 26B A4B (Q4_K_M, ~18 GB). Smaller tool-capable models (e.g. `llama3.2:3b`) technically run but **do not reason well enough to invoke tools autonomously** — they will answer "I don't see the file" rather than calling `read_file`. Investigation quality collapses. Always target a 26B+ class model in production.

## Known gotchas

- **Rate limits on free tier** — a full investigation needs ~100+ turns. ChatGPT free login caps out long before. Use Plus/Pro or `OPENAI_API_KEY`.
- **Shared auth across sub-agents** — the OAuth token is shared between orchestrator and spawned agents. Rate limits apply to the sum.
- **`wire_api = "chat"` deprecated** — do not add an Ollama/llama-server entry under `[model_providers.*]` in `config.toml`; Codex 0.122 rejects it. Use `--oss` instead.
- **Localhost RLM checks under sandbox** — approve localhost access before running `integrations/rlm/run_rlm.py` against Ollama; otherwise the model can be falsely reported unavailable.
