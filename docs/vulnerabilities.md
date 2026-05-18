# Vulnerabilities And Shell Safety

Lars Adrian Giske flagged a serious class of Spotlight v2 risks: unsafe shell command construction in skill instructions.

The issue was not a single exploit. It was a pattern: examples interpolated user-controlled or model-controlled values directly into shell commands.

## Risk

Values such as URLs, DOI identifiers, project names, search queries, timestamps, filenames, paths, social-media handles, and monitor criteria may come from:

- the user,
- model output,
- scraped pages,
- config files,
- generated findings,
- filesystem paths.

If these values are inserted into a shell string, crafted input can change command behavior. Risky characters and patterns include:

- semicolons,
- quotes,
- newlines,
- `$()`,
- backticks,
- leading dashes,
- path traversal,
- absolute paths outside the case or vault root.

The exact escape behavior depends on shell, wrapper, operating system, tmux/session handling, and runtime adapter. "It works locally" is not a sufficient safety argument.

## V2 Mitigation

Spotlight adds a `shell-safety` skill and `scripts/spotlight_safe.py`.

The helper validates:

- URLs,
- DOI identifiers,
- timestamps,
- slugs,
- filesystem paths,
- destructive-operation targets.

It also enforces base-directory containment for paths and rejects unsafe control characters or shell metacharacters.

## Safer Patterns

Prefer:

- argument arrays or helper scripts,
- stdin,
- temp files,
- JSON request bodies,
- `curl --get --data-urlencode`,
- path resolution before file operations,
- destructive-operation probes before deletion or overwrite.

Avoid:

- string-built shell commands with untrusted values,
- inline JSON assembled by concatenation,
- user-controlled flags,
- one-shot destructive commands,
- unprobed `rm`, `cp`, `mv`, overwrite, archive extraction, or lock cleanup.

## Destructive Operation Rule

Before destructive work:

1. Resolve the path.
2. Confirm it is inside the expected base directory.
3. Run a non-destructive probe using the same resolved target.
4. Inspect the probe output.
5. Run the real operation.
6. Record both the probe and the operation in the log or evidence bundle.

## Affected Areas

The v2 audit covered:

- content access,
- web archiving,
- monitoring,
- social media intelligence,
- OSINT Navigator calls,
- Junkipedia calls,
- Scoutpost calls,
- Unpaywall calls,
- ingest lock cleanup,
- recovery docs.

Regression coverage lives in `tests/shell-safety-check.py`.
