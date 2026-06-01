# SkillOpt-Lite

SkillOpt-lite is the bounded validation harness for improving Spotlight skills.
It is deliberately conservative: candidate edits must be small, scored on held
out fixtures, and reviewed as human-readable diffs before acceptance.

## Layout

- `fixtures/train/` — examples used to diagnose repeated failures.
- `fixtures/selection/` — held-out examples used to accept or reject candidate edits.
- `fixtures/test/` — final reporting only; never use to choose edits.
- `graders/` — deterministic graders for skill-specific outputs.
- `runs/` — local run artifacts. Ignored except for `.gitkeep`.

## Acceptance Rule

Accept a candidate only when:

- selection score strictly improves;
- no protected category regresses;
- `bash tests/smoke.sh` passes;
- `bash tests/eval.sh` passes;
- the candidate diff is reviewed by a human.

Ties are rejected.

## Protected Categories

- false high confidence
- unsupported public claim
- missing source URL
- fact-checker independence break
- sensitive-mode network/tool leakage
- destructive shell instruction
- secret harvesting or credential access
