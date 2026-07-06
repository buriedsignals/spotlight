# Spotlight skills — authoring contract

Spotlight's skills are consumed by two orchestration paths:
- **Frontier (skill-based):** Claude Code / Codex / Gemini self-drive, pulling skill bodies via `invoke-skill(...)`.
- **Non-frontier (Pi + Flue harness):** local Gemma / Fireworks GLM-5.2, where each sub-agent is composed with only its skill subset and bodies load **on invoke**, not up front.

Both depend on the same contract below. Breaking it re-inflates the context floor and can make Flue reject a skill.

## Hard rules (Agent-Skills spec — agentskills.io)

1. **Every `SKILL.md` has YAML frontmatter with `name` and `description`.** The description is the *load decision* — it must be enough to decide whether to pull the body **without** reading the body. Keep it one line, action-oriented, ≤ ~40 tokens.
2. **The body is self-contained.** A skill must be understandable and usable without any *sibling* skill's body already in context. Do **not** write "as defined in the X skill" expecting X to be loaded.
3. **Cross-skill handoffs go through `invoke-skill("x")`, never assumed presence.** If your skill needs another, point to it: `invoke-skill("follow-the-money")`. That's progressive disclosure — the pointer costs ~nothing; the body loads only when actually needed.
4. **Reference material lives in `references/` within the same skill dir** and is pulled on demand — not inlined into the body. Big procedures, formats, and tables belong there.
5. **Name a skill that actually exists.** A pointer to a non-existent skill is a conformance defect (it breaks self-containment and misleads the loader).

## Why this matters — the measured floor (baseline, 2026-07-06)

The current `opencode` path loads roles **plus every skill body** up front:

| Component | ~tokens (char/4 approx) |
|---|---|
| Role: `agents/investigator.md` | ~7.0K |
| Role: `agents/fact-checker.md` | ~3.9K |
| Orchestrator: `skills/spotlight/SKILL.md` | ~8.9K |
| **15 other skill bodies (on-invoke-eligible)** | **~34.0K** |
| **Total floor** | **~53.8K** |

That ~53.8K is why a vanilla 12B collapses and the 31B is ~9 min to first token. The `~34K` of non-orchestrator bodies is exactly what **on-demand loading (D1)** removes, and **per-agent composition (D2)** trims further — target floor **≤ ~20K**. The measurement script is the sum of frontmatter-stripped `SKILL.md` bodies; re-run it after any skill edit and keep the total from creeping back up.

## Per-skill checklist before merge

- [ ] Frontmatter `name` + `description` present; description is a standalone load decision.
- [ ] Body reads correctly with **no** sibling skill in context.
- [ ] All cross-skill references use `invoke-skill("<existing-skill>")`.
- [ ] Heavy detail is in `references/`, not the body.
- [ ] Body token count is justified — the floor is a shared budget.
