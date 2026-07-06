# PRD: Spotlight harness — dynamic skill loading, Flue on Pi, cross-OS launcher

**Status:** draft (unstaged). **Owner:** Tom. **Date:** 2026-07-06.
**Relates to:**
- Skill *distribution* (which skills land per runtime, dedup, provenance): `engine/docs/plans/2026-07-06-amditis-skills-and-skill-architecture-prd.md` + `engine/internal/catalog/schema.go` (`runtimes`/`content_repo`/`kind`) + `bsig skills resolve --runtime`.
- Canonical installer: `tools/engine` (`bsig`) — meant to supersede `install-spotlight.sh`; the two currently coexist.
- Model evidence: `tools/benchmarks/model_benchmarks_spec.md` §5.9.
- Navigator CLI: `tools/navigator/data/app/cli.py` (replaces the `osint_navigator` MCP — see Workstream F).
- Remaining execution: `tools/spotlight/TODOS.md`.

**Flue grounded (firecrawled 2026-07-06, `flueframework.com/docs`):** Flue is built on **Pi (`pi.dev`)** — the same Pi as Spotlight's `~/.pi/agent` (lineage risk retired). It **supports a local Ollama / OpenAI-compatible endpoint** via `registerProvider(api, baseUrl)` (local-serving risk retired). **Subagents are self-contained** (`defineAgentProfile` + `subagents:[…]`; "the parent's values never flow into the delegated session") — i.e. per-agent skill composition is native. It **discovers Agent-Skills (`SKILL.md`) directories under `<cwd>/.agents/skills/`** — the *exact* canonical store the engine places into (amditis PRD D3), so placed skills load with zero import. Runs on the **Node.js target (needs Node ≥ 22.19)** or Cloudflare.

> This PRD is **complementary** to the skill-distribution work. That system decides **which** skills are placed for each runtime (into `~/.pi/agent/skills/spotlight/`, `~/.config/opencode/skills/spotlight/`, …). This PRD covers **when/how those placed skills enter the model's context at runtime**, plus the Flue harness and its validation. It does **not** change placement.

---

## 1. Problem

Spotlight's harness loads its whole skill set + agent role files into context up front — a **~53K-token floor** (investigator ~7K + fact-checker ~4K + ~10 skill playbooks ~40K combined). Measured consequences (benchmarks §5.9):

- **Small models collapse.** Vanilla Gemma **12B** — strong single-shot (0.862 osint_qa, ethical, 26 tok/s local) — produces **empty output** on the real ~28 KB investigator prompt. Capability/prompt-density gap, *not* context-window (12B has 128K).
- **The 31B is slow.** It runs, but ~**9 min to first token** on a 50K context (~12 tok/s) — painfully slow for an agentic loop.

Two levers, one PRD:
1. **Dynamic skill loading** — load skill *bodies* on demand, not all up front. Harness-agnostic; drops the floor for every runtime; also speeds the 31B.
2. **Flue harness on Pi** — a structured multi-agent harness (Flue, built on Pi) that composes skills per-agent and restores the sub-agent separation bare Pi lacks.

## 2. Where this sits — two tiers, one harness each (decided 2026-07-06)

Within the **`spotlight` runtime family** (distinct from Mycroft/Goose), the split is now by **model tier**, and each tier has exactly one orchestration path:

| Tier | Runtimes / models | Orchestration | Rationale |
|---|---|---|---|
| **Frontier** | Claude Code, Codex, Gemini (subscription CLIs) | **① Skill-based** — the model self-drives with native sub-agent spawning + progressive `SKILL.md` disclosure | capable enough to self-orchestrate; keeps the subscription and the mature native agent loop. **Unchanged.** |
| **Non-frontier** | **Fireworks cloud** (GLM-5.2, ZDR) **and any local model** (Gemma 31B/12B) | **② Harness-based — Pi + Flue** | weaker/slower models can't self-drive; Flue supplies the orchestration + per-agent skill composition. **The one non-frontier harness.** |

**`opencode` is removed as a harness** (D7): Pi + Flue subsumes both places it was used — the cloud-Fireworks path and the local path.

Shared substrate (already built): catalog → `bsig skills resolve --runtime` → per-runtime placement. This PRD **consumes** that placement; it changes only when skill *bodies* load.

**The one-command entry point (confirmed correct, needs hardening).** `install-spotlight.sh` writes a single `spotlight` shell command that dispatches by the runtime chosen at configure time — `claude`→Claude Code, `gemini`→Gemini, `codex`→Codex, `opencode`→opencode, `local`→`spotlight-local` (opencode/pi + the configured local or cloud model, e.g. GLM-5.2). So one command loads the *right runtime and the right orchestration mode* automatically — the design you want. **Two problems this PRD must fix:** (a) it's a shell-RC edit that is **Homebrew-dependent even on Linux** (the likely Ubuntu install failure); (b) it's created by the standalone installer, **not** yet routed through `tools/engine` (`bsig`), which is the canonical cross-platform installer meant to own this. Cross-OS parity should converge on the engine path (Workstreams D–E).

## 3. Objectives / non-goals

**Objectives**
- **O1** — Cut the harness-mode context floor from ~53K to **≤ ~15–20K** via on-demand skill-body loading + per-agent composition. *(U1 measured baseline, 2026-07-06: **~53.8K** = roles ~19.8K [investigator 7.0K + fact-checker 3.9K + orchestrator 8.9K] + 15 on-invoke-eligible skill bodies **~34.0K**. The ~34K is what D1/D2 removes. See `skills/README.md`.)*
- **O2** — A **Flue-on-Pi** harness running Spotlight's orchestrator/investigator/fact-checker as composed agents against a local (or cloud) model, consuming the placed skills.
- **O3** — **Validate on Gemma 31B (the control)**: confirm the floor drop + a measurable speed gain, no quality/ethics regression — *before* investing in the 12B tune.
- **O4** — **The `spotlight` command works identically across OSes.** One command → right runtime → right orchestration mode, verified on macOS + Ubuntu/Debian + WSL, ideally routed through `tools/engine` rather than the Homebrew-dependent shell installer.
- **O5** — **Repeatable clean-install tests** on fresh environments (Docker + a wipeable GPU box for local-model runs).
- **O6** — **Methodology tool-discovery moves from the `osint_navigator` MCP to the Navigator CLI**, integrated into the investigation pipeline.
- **O7** — **Fetching moves off Firecrawl to open-source scraping**: Crawl4AI as primary, Scrapling (Camoufox stealth) as the escalation fallback on bot-block/empty result — removing the paid scraping key (not all keys; see Workstream G).

**Non-goals**
- No change to the skill-distribution / resolver / placement system (engine PRD owns it).
- No change to Mycroft/Goose.
- No change to the skill-based (Claude/Codex) path beyond confirming it still works.
- **Not** shipping a 12B tier in this PRD — gated on validation (see `TODOS.md`).
- No new *runtimes* beyond Flue/Pi; no Windows-native (WSL is the Windows story).

## 4. Design

**D1 — Skills load as manifest-first (name + description always; body on demand).** Every `SKILL.md` already has frontmatter (name, description, triggers). The harness holds only the manifest up front (~50–100 tok/skill); the full body loads when an agent invokes the skill. This is Claude Code's progressive-disclosure model applied to the Pi/opencode path. *Requirement:* frontmatter is sufficient for the load decision; bodies are self-contained.

**D2 — Per-agent skill composition (the "lightening").** The resolver yields the Spotlight skill set for the `pi` runtime; Flue composes **subsets per sub-agent** — investigator gets research/OSINT skills, fact-checker gets verification skills, orchestrator gets planning. No agent holds all ~10. This lives in the harness adapter, **never in the skills**.

**D3 — Flue as the Pi harness (multi-agent + durable).** Rebuild the `pi` local path as a Flue app: orchestrator + `defineAgentProfile` **subagents** (investigator, fact-checker), each with its own self-contained `skills:[…]` (grounded: Flue subagents don't inherit the parent's skills), `model` pointed at the local llama-server via `registerProvider(api:'openai-compatible', baseUrl:'http://127.0.0.1:…')` or a cloud model. Restores the verification independence bare Pi lacks; durable streams give resume-on-crash. Placed skills need **no import** — Flue auto-discovers `SKILL.md`s under `<cwd>/.agents/skills/`, which is the engine's canonical store, so the existing placement pipeline feeds Flue directly.

**D4 — RLM stays as case-data pre-pass.** Unchanged: RLM (`gemma4:e4b`) distills raw sources → lead JSON before the investigator runs. It shrinks **case-data** context, *not* the system-prompt floor (D1/D2 own that). Together they keep total context small over long runs.

**D5 — Invariant.** Skills never assume their caller; all loading/composition logic lives in the harness adapter. A `SKILL.md` edit ships to **both** orchestration modes for free.

**D6 — One dispatcher, engine-owned.** The `spotlight` command (runtime → launch target) is a real strength — keep it, but move its authorship from the shell-RC installer to `bsig` so a single cross-platform code path produces it (macOS + Linux). Post-migration dispatch table: `claude` / `codex` / `gemini` (frontier, skill-based) and `flue` (non-frontier: Fireworks **or** local, via Pi). No `opencode`.

**D7 — Retire `opencode`; Pi + Flue is the sole non-frontier harness. *(Remove only once the harness works.)*** `opencode` is removed from the Spotlight config surface — `cloudRuntime` enum, `localAgent` enum (`SERVER_FOR_AGENT`), `configure.html`, `install-spotlight.sh`, and the engine's runtime set — **but only after the Flue/Pi harness is working and passing on both Fireworks and a local model.** Today opencode is the *only* working harness for both non-frontier paths (the pinned GLM-5.2 `opencode.json` cloud path + the local `opencode`→ollama path); pulling it before its Flue/Pi replacement lands would leave both tiers harness-less. So the removal executes *inside* Workstream B as a swap (move the Fireworks/local wiring onto Flue/Pi, verify, then delete opencode), never a standalone deletion.

**D8 — Frontier stays skill-based; never routed through Flue.** Claude Code / Codex / Gemini are subscription CLIs that self-orchestrate with native sub-agent spawning — they keep the skill-based path. Flue/Pi is only for the non-frontier tier (models that can't self-drive). The frontier CLIs are not driven through Flue.

## 5. Workstreams

**A — Dynamic skill loading (the floor).** *Highest leverage, no Flue dependency.*
- Audit: does opencode/pi load skill *bodies* up front or on-demand today? (Claude Code is progressive already.)
- Make frontmatter load-decision-sufficient; bodies self-contained.
- Implement manifest-only up-front load + on-invoke body load in the harness.
- **Measure** the floor before/after on the same investigator prompt (target ≤ ~20K). Also measure the 31B's TTFT delta (less prompt-eval).

**B — Flue harness on Pi.** *(Pi-lineage + local-serving now grounded — see header; this is a build, not a spike.)*
- Confirm the installed `~/.pi/agent` version matches Flue's `pi.dev` provider/runtime contract (version pin).
- Register the local endpoint (`registerProvider` → llama-server) in `src/app.ts`; keep the cloud provider (GLM-5.2 on Fireworks) available through the same specifier scheme.
- Port orchestrator + investigator + fact-checker `defineAgentProfile` subagents with per-agent composed skills (D2), discovering `<cwd>/.agents/skills/spotlight` (D3) — set Spotlight's working dir so discovery resolves the placed store.
- Sandbox + durability config; wire `session.task(...)` delegation for the fact-check independence.
- **Swap out opencode (D7):** move the Fireworks/GLM-5.2 pin and the local-model wiring from `opencode.json` onto the Flue/Pi provider config; then remove `opencode` from `cloudRuntime`/`localAgent` enums, `configure.html`, `install-spotlight.sh`, and the engine runtime set — **only once the Flue/Pi path passes on both Fireworks and a local model**, so neither tier is ever harness-less.

**C — Validate on Gemma 31B (the gate).**
- Run a real Spotlight investigator task on **Gemma 31B via the new Flue/Pi harness + dynamic loading**.
- **Measure vs the current opencode/31B baseline:** (a) context floor (tokens), (b) TTFT + tokens/s, (c) completes the loop, (d) output quality (`methodology.json` schema + investigative soundness), (e) Q9 ethics unchanged.
- **Success** = floor ≤ ~20K, materially faster TTFT, no quality/ethics regression. This proves the harness change is real **before** any 12B tuning spend.

**D — Cross-OS `spotlight` launcher (the Ubuntu fix).**
- Reproduce the reported Ubuntu failure; root-cause the Homebrew dependency (installer is nominally Linux-OK but installs deps via `brew`). Replace Linux dep bootstrap with the native package path (apt/dnf) or make deps engine-managed.
- Move `spotlight`-command authorship into `bsig` (engine) so one cross-platform path emits it; keep the runtime→launch dispatch (add `flue`). Verify the smart dispatch (right runtime + right orchestration) on macOS, Ubuntu/Debian, and WSL.
- Doctor parity: `spotlight-doctor` (or `bsig doctor --product spotlight`) passes on each OS.

**E — Fresh-environment install/test harness.**
- **Docker:** clean `ubuntu:latest` / `debian` images → run the installer (or `bsig`) non-interactively → assert `spotlight` command present, correct runtime wired, doctor green, one dry-run investigation. Add to CI as the drift gate for cross-OS install.
- **Wipeable GPU box (Runpod or equivalent Tom has):** for the paths Docker can't cover — local model pull + llama-server load + a real Flue/Pi run on Gemma 31B/12B. Snapshot/wipe between runs for reproducibility.
- These tests are the acceptance evidence for O4/O5 and for the 12B validation in `TODOS.md`.

**F — Navigator MCP → CLI (methodology tool-discovery).**
- Today OSINT-Navigator tool/methodology lookups run through the `osint_navigator` **MCP** block (written into the harness config by `install-spotlight.sh`).
- Replace with the **Navigator CLI** (`tools/navigator/data/app/cli.py`) invoked as a pipeline step / tool call, now that the unified CLI exists — better fit for the investigator's plan→tool-discovery→execute loop, one fewer long-lived MCP process, and CLI output is easier to fold into `methodology/`.
- Keep the `OSINT_NAV_API_KEY` contract; update setup + docs; regression-test tool lookups against the MCP behaviour before removing the MCP block.

**G — Crawl4AI (primary) + Scrapling (stealth fallback) — replace Firecrawl.**
- Today the primary web fetch requires `FIRECRAWL_API_KEY` (a `:?` guard blocks install without it) — a paid, closed service on the critical path of a tool meant to run locally and privately. Scoutpost already ported to open-source **Crawl4AI** (PR #262, benchmarked in `tools/benchmarks`; Crawl4AI & Scrapling both 100% on hostile civic/registry cases).
- **Decision (2026-07-06): escalation ladder, both in-scope.** **Crawl4AI primary** — cleaner markdown for evidence, faster, cleared 100% of the benchmark, org-consistent with Scoutpost. **Scrapling (Camoufox stealth) as the fallback** on bot-block / empty result — Spotlight's sources are *more* adversarial than Scoutpost's, so the stealth tail matters here; the reason Scoutpost rejected Scrapling (its Deno-edge HTTP constraint) doesn't apply to Spotlight's local Python process.
- **Import directly, not as Scoutpost's sidecar.** Spotlight is local Python — it imports `crawl4ai`/`scrapling` as libraries behind a provider-agnostic seam (lifted from Scoutpost `_shared/scrape.ts`), skipping the FastAPI/Render/auth layer. Lift the **raw-markdown** mapping (not `fit_markdown` — it wrecked evidence pages) and the **deterministic PDF path** (pdftotext → optional Gemini-on-low-density).
- **Invert Scoutpost's default:** `crawl4ai` becomes the default provider; the `FIRECRAWL_API_KEY` guard requires the key *only* when Firecrawl is explicitly selected. The installer provisions the browser engines (Chromium + Camoufox) + poppler, cross-OS (via the cross-OS bootstrap).
- **Caveat for comms:** this removes the *scraping* key, not all keys — `OSINT_NAV_API_KEY` remains unless Navigator is made optional. So "no paid scraping / open-source fetching" is true; "zero API keys" is not (yet).

## 6. Success criteria
- Harness-path context floor **≤ ~20K** tokens (from ~53K).
- Gemma 31B on Flue/Pi: measurably faster TTFT than opencode/31B; completes a full run; no ethics/quality regression.
- Skill edits require no harness change; the skill-based (Claude/Codex) path is unaffected.
- `spotlight` command installs and dispatches correctly on **macOS, Ubuntu/Debian, WSL**; a fresh-container install test passes in CI.
- Methodology tool-discovery runs through the Navigator CLI with parity to the old MCP.

## 7. Risks
- **Flue is 1.0-beta** — betting a production harness on beta. Mitigate: pin a version; keep opencode as the fallback harness until Flue proves out.
- **Runtime sprawl:** Goose + opencode + Flue/Pi. Decide *replace-opencode* vs *add-tier* after Workstream C, not now.
- **Composition starvation:** a sub-agent missing a skill it needed (mitigate: conservative bundles + on-demand pull).
- **Installer bifurcation:** the shell installer and `bsig` can drift; O4 must land the `spotlight` command in **one** place (the engine) or the Ubuntu class of bug recurs.
- *(Retired by grounding: Pi-lineage — Flue is `pi.dev`, same as `~/.pi/agent`; local-endpoint support — `registerProvider` drives a local OpenAI-compatible server.)*

## 8. Sequencing
1. **Workstream A** first — cheapest, helps the 31B and every runtime, zero Flue commitment.
2. **Workstream D + E** (cross-OS launcher + fresh-env tests) — unblock the reported Ubuntu users and give every later step a clean-room to prove itself in. Can run parallel to A.
3. **Workstream B** — after A proves the floor drops.
4. **Workstream F** (Navigator CLI) — independent; fold in during B (pipeline touch).
5. **Workstream C** — the gate. Only after C passes do we invest in the 12B tune (`TODOS.md`).

## 9. Cross-project execution & hygiene

This PRD spans **four repos** — `spotlight` (harness), `engine` (installer/dispatch, skill placement), `navigator` (CLI), `benchmarks` (model evidence). Coordinate it as one plan, and keep the tree clean as it lands:

- **`ce-plan`** (`kit/compound-engineering`) — before implementing, run it to produce the thorough cross-project overview: what changes in each of the four repos, the dependency order (engine dispatch ↔ spotlight harness ↔ navigator CLI), and the shared skill/placement contract. This PRD is the input; `ce-plan` turns it into the execution graph so nothing is implemented in the wrong order or in isolation.
- **`ce-simplify`** — after each workstream, run it to collapse the redundancy this touches (e.g. the shell-installer vs engine dispatch duplication, the MCP→CLI removal, dead launcher branches) so we don't leave two ways to do the same thing.
- **`ce-docs`** — keep the docs current as we go: this PRD, `TODOS.md`, the Spotlight `AGENTS.md`/`docs/`, the engine SPEC's installer section, and the Navigator integration notes. Docs update in the same change that alters behaviour, not after.

**Definition of done for the whole PRD:** Workstreams A–F merged; fresh-container + wipeable-GPU tests green; `ce-simplify` leaves no duplicate installer/dispatch/MCP paths; `ce-docs` shows every affected doc updated; the 12B decision is handed to `TODOS.md` with the 31B gate (C) passed.
