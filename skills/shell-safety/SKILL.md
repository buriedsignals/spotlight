---
name: shell-safety
description: Portable safe command construction for any agent skill or recipe. Use before a shell call containing user, model, scraped, configuration, filesystem, or generated values.
version: "2.0"
invocable_by: [orchestrator, investigator, fact-checker]
---

# Shell Safety

Use this skill before any shell operation that includes data from a user, model,
scraped page, email, social post, configuration file, filesystem path, or
generated output. Treat all such material as untrusted input.

## Non-Negotiable Rules

1. Do not build shell commands by interpolating untrusted strings.
2. Prefer helper scripts that receive arguments as argv, stdin, environment variables, temp files, or structured JSON.
3. Validate values before command construction.
4. Do not let user input select flags, command separators, shell operators, or output paths outside the allowed base directory.
5. Treat copy, move, overwrite, delete, archive extraction, and lock cleanup as destructive.

## Validation Helper

Run the helper from the Spotlight checkout root, through the Spotlight
interpreter (never a bare `python3`):

```text
scripts/spotlight-python scripts/spotlight_safe.py validate-url "<url>"
scripts/spotlight-python scripts/spotlight_safe.py validate-doi "<doi>"
scripts/spotlight-python scripts/spotlight_safe.py validate-timestamp "<timestamp>"
scripts/spotlight-python scripts/spotlight_safe.py validate-slug "<slug>"
scripts/spotlight-python scripts/spotlight_safe.py resolve-path --base "<allowed-base>" --path "<candidate>"
scripts/spotlight-python scripts/spotlight_safe.py destructive-probe --base "<allowed-base>" --path "<candidate>"
scripts/spotlight-python scripts/spotlight_safe.py sha256 "<file>"
```

This is the one supported entry. It runs this skill's
`scripts/shell_safe.py`, so do not call that file directly.

The helper rejects shell metacharacters in identifiers, invalid URL schemes, `&` outside a URL's query string (always pass a URL double-quoted), path traversal outside the allowed base, leading-dash path segments, NUL bytes, and unsafe destructive targets.

## Curl Guidance

Do not write examples like:

```text
curl "https://example.test?q={user_query}"
```

Use one of these instead:

- `curl --get --data-urlencode "q=<value>" https://example.test/search`
- a Python helper that serializes JSON with `json.dumps`
- a temp JSON file passed as `--data @file.json`

## CLI Argument Guidance

Passing an untrusted string inside a shell-quoted CLI argument still lets the
shell interpret quotes, backticks, and substitutions. Prefer stdin, a validated
temporary file, environment variables, or structured JSON. If a downstream
tool accepts only one quoted argument, reject NUL, line breaks, backticks,
`$`, `;`, `&`, `|`, `<`, and `>` before invocation.

## Destructive Operations

Before destructive work:

1. Resolve paths.
2. Confirm every path is inside the expected base.
3. Run `destructive-probe` or an equivalent non-destructive probe using the same resolved arguments.
4. Record the probe output.
5. Run the real operation only after the probe is inspected.
6. Record the real operation in the run log or evidence bundle.

Never provide one-shot destructive commands in skill instructions.
