<!--
Thanks for contributing. Fill this out so we can review faster.
See CONTRIBUTING.md for commit conventions and the subsystem patterns.
-->

## Summary

One or two sentences on what this PR does and why.

## Type of change

- [ ] Bug fix
- [ ] New feature (skill / integration / feed source / runtime / UI)
- [ ] Docs only
- [ ] Refactor / cleanup
- [ ] Breaking change (bumps major version)

## What changed

- File-by-file or subsystem-by-subsystem. Point at the key files so a reviewer can navigate.

## Testing

- [ ] `bash tests/smoke.sh` passes
- [ ] `bash tests/eval.sh` passes
- [ ] For `setup.html` changes: generated bash scripts pass `bash -n` for all runtime configs
- [ ] For new skills: smoke-test skill count incremented in `docs/structure.md` and `AGENTS.md`
- [ ] For new integrations / feeds: `preflight.py --text` output includes the new entry
- [ ] Manual testing done: describe what you actually ran

## Docs

- [ ] Updated relevant docs in `docs/` (if this changes user-visible behavior)
- [ ] Updated `CHANGELOG.md` under `## [Unreleased]` (or noted why not)
- [ ] Added / updated attribution in `LICENSE` if synthesizing external methodology

## Sensitive mode

- [ ] Considered sensitive-mode implications (if this touches network verbs or remote API calls)
- [ ] N/A — this PR doesn't affect runtime behavior

## Breaking changes

If this is a breaking change, describe the migration path. Who needs to update their `.spotlight-config.json` / `.env` / vault?
