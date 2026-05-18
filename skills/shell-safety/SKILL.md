---
name: shell-safety
description: Safe command construction for Spotlight skills. Use before any execute-shell call that includes user, model, scraped, config, filesystem, or generated values; validates URLs, DOI identifiers, paths, timestamps, filenames, and destructive-operation probes.
version: "1.0"
invocable_by: [investigator, fact-checker, orchestrator, user]
---

# Shell Safety

Use this skill before any `execute-shell` operation that includes data from a user, model, scraped page, config file, filesystem path, or generated finding.

## Non-Negotiable Rules

1. Do not build shell commands by interpolating untrusted strings.
2. Prefer helper scripts that receive arguments as argv, stdin, environment variables, temp files, or structured JSON.
3. Validate values before command construction.
4. Do not let user input select flags, command separators, shell operators, or output paths outside the allowed base directory.
5. Treat copy, move, overwrite, delete, archive extraction, and lock cleanup as destructive.

## Validation Helper

Use `scripts/spotlight_safe.py` for reusable validation:

```text
python3 scripts/spotlight_safe.py validate-url "<url>"
python3 scripts/spotlight_safe.py validate-doi "<doi>"
python3 scripts/spotlight_safe.py resolve-path --base "<allowed-base>" --path "<candidate>"
python3 scripts/spotlight_safe.py destructive-probe --base "<allowed-base>" --path "<candidate>"
```

The helper rejects shell metacharacters in identifiers, invalid URL schemes, path traversal outside the allowed base, leading-dash path segments, NUL bytes, and unsafe destructive targets.

## Curl Guidance

Do not write examples like:

```text
curl "https://example.test?q={user_query}"
```

Use one of these instead:

- `curl --get --data-urlencode "q=<value>" https://example.test/search`
- a Python helper that serializes JSON with `json.dumps`
- a temp JSON file passed as `--data @file.json`

## Destructive Operations

Before destructive work:

1. Resolve paths.
2. Confirm every path is inside the expected base.
3. Run `destructive-probe` or an equivalent non-destructive probe using the same resolved arguments.
4. Record the probe output.
5. Run the real operation only after the probe is inspected.
6. Record the real operation in the investigation log or evidence bundle.

Never provide one-shot destructive commands in skill instructions.
