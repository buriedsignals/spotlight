# Spotlight skills — authoring contract

Spotlight exposes a manifest-scoped skill catalog across Claude Code, Codex,
Gemini, OpenCode, Pi, and Flue. Compatible runtimes discover skill metadata
first and load a `SKILL.md` body only when the skill is invoked. Role prompts and
harness composition guide which skills an agent should use; not every runtime
enforces a separate role-filtered catalog.

This README documents the authoring contract. It is not discovered or loaded as
a skill.

## Agent Skills compatibility

Each skill follows the [Agent Skills specification](https://agentskills.io/specification):

1. Store the skill at `skills/{name}/SKILL.md`.
2. Include YAML frontmatter with `name` and `description`. The name must match
   the containing directory.
3. Use the description as a standalone routing decision: state what the skill
   does and when it should be invoked.
4. Keep supporting procedures, formats, and tables in the skill's own
   `references/` directory and load them only when needed.

## Spotlight constraints

1. A skill body must be usable without assuming that a sibling skill is already
   in context.
2. Cross-skill handoffs must explicitly use `invoke-skill("<existing-skill>")`.
3. Every referenced skill must exist in the resolved catalog.
4. Keep skill bodies focused. Progressive loading only reduces context use when
   optional detail remains in on-demand references.

## Distribution boundaries

- `AGENTS.md` is the runtime contract and skill registry.
- `skills.manifest` is the engine-resolved installation and discovery boundary.
- `skills-manifest.json` is the maintenance and phase-assignment contract.
- `harness/composition.json` records the intended role-to-skill bundles.
- `plugins/spotlight/` is generated from the canonical repository sources; do
  not edit its copied skills directly.
