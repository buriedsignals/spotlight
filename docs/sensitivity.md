# Sensitivity — separate ingest vault + dual-query command

Status: design doc — not yet implemented.
Scope: deliberately narrow. See "What this is not" below.

---

## What this is

Spotlight should support **two ingest targets**:

- The **default vault** + default QMD index. This is the existing
  behaviour. Anything a journalist runs through the standard
  Spotlight pipeline ends up here.
- A **separate sensitive vault** + separate QMD index. Opt-in. The
  ingest skill writes here when given an explicit flag.

And **one new query command** that's only available in local-runtime
sessions:

- A wrapper that queries **both** indices and unions the results.
  Standard `qmd ...` in any other context queries the default index
  only.

That's it. Two ingest targets, one union-query wrapper.

## Source expressions do not change the security boundary

The source-expression pilot adds exact quoted text, attribution, case-local
paths, and hashes to a case. That improves auditability, but can also make a
case artifact more revealing. It provides no encryption, access control,
anonymization, redaction, or confidentiality guarantee.

When the runtime's existing `sensitive` mode disables external egress,
expressions may be created only from already present case-local material. A
missing local source or inspectable text derivative remains a gap; sensitive
mode is never a reason to reconstruct wording from memory. The same exact-text,
hash, and reference checks apply, while truth and entailment remain human and
fact-check responsibilities.

Case directories remain outside the two-vault design in this document. Store
and protect a case containing expressions according to the newsroom's actual
threat model. If the proposed sensitive ingest target is implemented,
expression snapshots must stay embedded only in eligible sensitive claim notes
and must not create cross-vault links or a standalone expression registry.
That separation is convenience plumbing, not a confidentiality boundary.

## What this is not

This design is explicitly **not**:

- A workflow restriction on cases.
- An enforcement boundary between frontier and local runtimes.
- A protection against an adversarial agent with shell access.
- A guarantee that sensitive material can't leak.

Spotlight is an OSINT investigation orchestrator for open material.
**Sensitive investigations happen elsewhere** — in whatever
environment the journalist deems appropriate (local model on an
isolated machine, encrypted disk, air gap, paper). The output of that
work can optionally be ingested into the sensitive vault so it's
discoverable from future local-runtime Spotlight sessions. Spotlight
itself does not orchestrate the sensitive investigation.

The earlier draft of this doc proposed `spotlight escalate`,
`spotlight declassify`, frontier-runtime hard-blocks, UID separation,
permission templates, and a six-layer enforcement chain. That design
is dropped. The verification work at
`~/buried_signals/investigations_test/` (2026-05-22) showed that
launcher-level wrappers don't survive an agent with raw shell access
without UID separation, and UID separation is too much install-time
machinery for the value delivered. We are not in the business of
building a counter-intelligence system.

## Two ingest targets

The ingest skill currently writes to one vault path determined at
install time. Add a second target.

**Configuration** (in `.env` or `.spotlight-config.json`):

```
SPOTLIGHT_VAULT_PATH=~/Obsidian/main          # existing
SPOTLIGHT_SENSITIVE_ENABLED=1                 # new — opt-in flag
```

That's the only field the journalist sets. Everything else is derived
by convention:

- `SPOTLIGHT_SENSITIVE_VAULT_PATH = "${SPOTLIGHT_VAULT_PATH}-sensitive"`
  (so the default lands at `~/Obsidian/main-sensitive` next to the
  open vault — same parent dir, same naming root, suffix `-sensitive`).
- `SPOTLIGHT_SENSITIVE_INDEX = "$(basename "$SPOTLIGHT_VAULT_PATH")-sensitive"`
  (QMD index name follows the vault name; for `~/Obsidian/main` that's
  `main-sensitive`).

Both can be overridden explicitly if the journalist wants the sensitive
vault somewhere unusual (e.g. on an encrypted volume), but the default
is zero-config.

If `SPOTLIGHT_SENSITIVE_ENABLED` is unset or `0`, the sensitive ingest
path is disabled and Spotlight behaves exactly as today.

**Invocation** — the ingest skill accepts an optional flag:

```
invoke-skill ingest --target sensitive
```

When `--target sensitive`, the skill:

1. Writes notes to `SPOTLIGHT_SENSITIVE_VAULT_PATH` instead of the
   default vault path.
2. After writing, runs `qmd --index <SPOTLIGHT_SENSITIVE_INDEX>
   collection add <path>` so the sensitive vault becomes
   independently queryable.

The two vaults never cross-link. Sensitive notes that reference an
entity also present in the default vault may include a textual
mention; they do not write a wikilink that resolves into the default
vault. (This is enforced by ingest behaviour, not by any runtime
gate.)

QMD's native `--index <name>` flag is enough machinery — the 2026-05-22
verification confirmed two named indices stay in separate sqlite
files at `~/.cache/qmd/<name>.sqlite`. No QMD patch required.

## Dual-query command

A wrapper, available in local-runtime sessions only, that queries the
default index and the sensitive index and merges results:

```
qmd-spotlight query "..."          # default behaviour: open index only
qmd-spotlight query "..." --with-sensitive    # union with sensitive index
```

Or, if the merge should be implicit when both indices exist:

```
local-qmd query "..."     # always unions if SPOTLIGHT_SENSITIVE_INDEX is set
```

Pick one shape during implementation. The second is friendlier; the
first is more auditable from logs.

The wrapper is installed on PATH only by the local-runtime launcher
(`~/.local/bin/spotlight-local`). A frontier-runtime launcher does
not install it.

### Runtime check inside the wrapper

In addition to the PATH-only install, the wrapper itself reads
`SPOTLIGHT_RUNTIME` (a new env var every spotlight launcher sets at
session start) and refuses if it's anything other than `local`:

```bash
# qmd-spotlight (sketch)
RUNTIME="${SPOTLIGHT_RUNTIME:-}"
case "$RUNTIME" in
  local|"")  # local session, or shell user running it manually — allow
    ;;
  *)
    echo "qmd-spotlight: refusing — SPOTLIGHT_RUNTIME='$RUNTIME', sensitive index is local-only." >&2
    exit 64
    ;;
esac
```

Two layers stacked:

1. **PATH** — frontier launchers don't put the wrapper on PATH at
   all. An agent that calls `qmd-spotlight` by bare name gets
   "command not found."
2. **Env check** — if the wrapper is discovered at its filesystem
   path and invoked directly, the env check fires. Frontier launchers
   set `SPOTLIGHT_RUNTIME=claude` (or `gemini`, `codex`, `opencode`);
   the wrapper sees a non-`local` value and refuses with an
   explanatory error.

### What this gate does and does not do

**It catches:** the launcher misconfigured, the agent that learned
about the wrapper's existence from documentation and tried to call
it, prompt injection that names the wrapper by guess, the journalist
who manually invoked the wrong launcher.

**It does not catch:** a frontier agent that runs `qmd --index
$(basename ${SPOTLIGHT_VAULT_PATH})-sensitive query "..."` directly,
bypassing the wrapper entirely. QMD itself does not honor
`SPOTLIGHT_RUNTIME`. Closing that bypass is what the cut UID-
separation phase was for, and we are not bringing it back.

Treat the env check as a "do not do this by accident" guard, not as a
confidentiality boundary. If the journalist runs frontier-runtime
sessions on the same machine where the sensitive index exists, they
accept that the frontier agent could in principle reach it via raw
`qmd`. The mitigation for that risk is operational (don't run
frontier sessions on the same machine, use a separate machine for
sensitive material) and lives outside Spotlight.

### `SPOTLIGHT_RUNTIME` is useful beyond this wrapper

This env var earns its keep for other reasons — skill prompts can
condition behavior on it ("if `SPOTLIGHT_RUNTIME=local`, prefer
locally-runnable tools"), the installer can use it to decide what to
clean up, debug output benefits. Adding it for the wrapper gate
means a primitive that's available everywhere else for free.

## Implementation surface

| Touch | Change |
|---|---|
| `.env` / config schema | Add `SPOTLIGHT_SENSITIVE_ENABLED` (the only field the journalist sets). Optional overrides `SPOTLIGHT_SENSITIVE_VAULT_PATH` and `SPOTLIGHT_SENSITIVE_INDEX` exist but default by convention to `<vault>-sensitive`. |
| All launchers (`spotlight-local`, `spotlight-claude`, etc.) | Export `SPOTLIGHT_RUNTIME` at session start. `local`, `claude`, `gemini`, `codex`, or `opencode`. |
| `skills/ingest/SKILL.md` | Accept `--target sensitive`. When set: write to the sensitive vault path; add the result to the sensitive QMD index instead of the default; refuse to cross-link into the default vault. Refuse entirely if `SPOTLIGHT_SENSITIVE_ENABLED=0`. |
| New: `~/.local/bin/qmd-spotlight` | Wrapper that queries the default index, optionally also the sensitive index. Env check at top refuses if `SPOTLIGHT_RUNTIME` is set to a frontier value. Installed by `install-spotlight.sh` only when the local runtime is selected. |
| `install-spotlight.sh` | When local runtime + `SPOTLIGHT_SENSITIVE_ENABLED=1`: ensure the sensitive vault directory exists at the convention-derived path, install the wrapper on PATH; the local configurator would offer an override of the default path/index name. |
| `docs/sensitivity.md` | This doc. |

That's the whole change. Roughly 50–100 LOC across config, ingest
skill, the wrapper, and the launcher env exports.

## Out of scope

- Anything that touches case directories or `case.yaml`.
- Any check on which runtime is active.
- Any blocking, gating, refusal, or warning by the launcher.
- Declassification, escalation, or any state transition between
  vaults. If a journalist wants to promote a sensitive note to the
  open vault, that's a manual file copy and a re-ingest — no special
  command.
- Provenance manifest entries for tier transitions.
- Multi-machine sync hygiene. The sensitive vault is the
  journalist's to manage; if they want it on iCloud they can put it
  on iCloud, and they own that decision.
- UID separation, sandbox profiles, permission templates, air gap.
  Those belong to the operational layer the journalist runs *around*
  Spotlight, not inside it.

## Why this is the right scope

The earlier design tried to make Spotlight responsible for keeping
sensitive material confidential. That is too large a promise for an
OSINT orchestrator to make honestly — confidentiality lives in the
operating environment, not in a workflow tool. By stripping the
restrictions and just providing the plumbing (two ingest targets,
one union query), Spotlight stays focused on what it's actually for
and lets the journalist compose their own sensitive workflow with
whatever tools genuinely match their threat model.

---

## FAQ

For readers who landed here without context on what Spotlight is and
what this design fits into.

**Source code:** <https://github.com/buriedsignals/spotlight>

### What is Spotlight?

Spotlight is an OSINT investigation orchestrator for journalists. It
runs on top of an AI agent runtime (Claude Code, Codex, Gemini,
OpenCode, Goose, Hermes, or a local model via Ollama / llama-server)
and walks the agent through a fixed pipeline: preflight → brief →
methodology → five execution cycles → fact-checking gate → ingestion
into a knowledge vault. The skills, agent prompts, schemas, and
verification rules are version-controlled markdown — the agent
follows them; the journalist owns them.

The point of the pipeline is **editorial accountability**: every
claim is grounded in scraped evidence, every source is archived
locally before it's cited, fact-checking runs as an independent pass
with its own verdict taxonomy, and confidence is gated by
access-method enforcement. The output of an investigation is a
findings JSON, a fact-check JSON, an evidence trail, and (optionally)
a knowledge-base ingest so subsequent investigations can build on
prior work via QMD search.

Spotlight does not replace the journalist. It removes the rote
work — link-walking, archive-before-citing, schema compliance,
fact-check provenance — so the journalist's time goes to editorial
judgement and source work.

### Why does the runtime matter?

Different runtimes have different trust properties. **Frontier
runtimes** (Claude, Gemini, OpenAI, OpenRouter) send the agent's
context to a third-party provider — fine for open material, not fine
for sensitive material. **Local runtimes** (Ollama or llama-server
running a model like
`tomvaillant/gemma4-e4b-abliterated-journalist-GGUF:Q4_K_M`) keep the
context on the journalist's machine.

The journalist picks the runtime per session, and the install script
configures both. There is no automatic switching.

### What is the sensitive vault flag this doc describes?

A flag in the install config that turns on a second, parallel ingest
target. With `SPOTLIGHT_SENSITIVE_ENABLED=1`:

- A second vault directory is created next to the main one, suffixed
  `-sensitive` (so the convention reads "same name, sensitive
  twin"). The journalist owns what goes in it.
- The ingest skill gains a `--target sensitive` mode that writes to
  the second vault + a second QMD index instead of the default.
- Local-runtime sessions get a `qmd-spotlight` wrapper on PATH that
  queries both indices and unions the results. Frontier-runtime
  sessions don't get the wrapper installed; if they discover it and
  try to call it anyway, an env-var check (`SPOTLIGHT_RUNTIME`)
  refuses with a clear error.

That's the whole feature.

### Is this a confidentiality guarantee?

No. The flag's purpose is **plumbing**, not enforcement. It lets a
local-model session reach sensitive material the journalist
deliberately put in a separate vault, without making that material
discoverable from a frontier-model session that just calls bare
`qmd`. It does not prevent a frontier agent on the same machine from
running `qmd --index <name>-sensitive` directly and reading the
sensitive vault that way.

If your threat model needs that level of guarantee, run sensitive
work on a separate machine (the existing Mac mini / Hermes path is
already in the Spotlight ecosystem for this) and treat the sensitive
vault on the laptop as a *summary archive* of work done elsewhere,
not as the workspace for sensitive investigation itself. The design
doc names this distinction in "What this is not" at the top of the
page.

### Why not enforce harder?

Earlier drafts of this doc proposed UID separation, runtime
permission templates, escalation/declassification state transitions,
case-level sensitivity fields, and frontier-runtime hard-blocks. The
2026-05-22 verification harness at
`~/buried_signals/investigations_test/` confirmed two things:

1. The dual-index *query* mechanic works (QMD's native `--index
   <name>` is sufficient — no QMD patch needed).
2. The launcher wrapper alone is bypassable by an agent with raw
   shell access. Closing that bypass requires UID separation, which
   is too much install-time machinery (creating a `spotlight-frontier`
   Unix account, group permissions, sudo plumbing) for an OSINT
   orchestrator to honestly justify.

Spotlight's job is to standardize the investigation workflow.
Confidentiality is an operational concern that lives outside the
tool. We provide the plumbing; the journalist composes the
operational layer (separate machine, encrypted disk, air gap, paper)
that matches their actual threat model.

### Where do I read more?

- Repo: <https://github.com/buriedsignals/spotlight>
- Architecture overview: [`docs/structure.md`](structure.md)
- Investigation pipeline: [`docs/investigating.md`](investigating.md)
- Fact-checking pass: [`docs/fact-checking.md`](fact-checking.md)
- Epistemic grounding: [`docs/epistemic-grounding.md`](epistemic-grounding.md)
- Runtime wiring: [`docs/runtimes.md`](runtimes.md)
- Disclaimer + scope limits: [`DISCLAIMER.md`](../DISCLAIMER.md) (in the repo root)
