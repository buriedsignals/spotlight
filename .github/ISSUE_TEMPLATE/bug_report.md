---
name: Bug report
about: Something didn't work as documented
labels: bug
---

## What happened

A clear, concise description. What did you try, what did you expect, what actually happened?

## Reproduce

Steps so we can hit the same bug:

1. …
2. …
3. …

## Environment

- OS: (macOS 14.x / Ubuntu 22.04 / WSL / …)
- Runtime: (pi / Claude Code / Gemini / Codex / OpenCode + provider / Local)
- Spotlight version / commit: `git rev-parse --short HEAD`
- Node version: `node --version`
- Python version: `python3 --version`
- Firecrawl installed: `firecrawl --version`

## Preflight output

```
# Paste output of:
# spotlight doctor
# or:
# python3 monitoring/feeds/preflight.py --text
# python3 integrations/preflight.py --text
```

## Investigation-log (if relevant)

If the bug surfaced mid-investigation, paste the last ~50 lines of `cases/{project}/data/investigation-log.json`. Redact anything sensitive.

## Screenshots / logs

If UI-related (setup.html, index.html, review.html), attach a screenshot.
If CLI-related, paste the full terminal output.

## Workaround

Did you find one? (Helps others hitting the same issue while we fix it.)
