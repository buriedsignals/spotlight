# Spotlight Validated Dependencies

Reviewed on: 2026-06-09

This file is the source of truth for packages the Spotlight setup flow may install from npm or PyPI. The setup page and `install-spotlight.sh` must install exact versions only. If a package is not listed here, the installer must stop and report that manual review is required.

## Default Setup Packages

| Ecosystem | Package | Version | Binary | Install Policy |
|---|---:|---:|---|---|
| npm | `firecrawl-cli` | `1.3.1` | `firecrawl` | Installed by setup if missing or if a different global npm version is present. |
| npm | `@tobilu/qmd` | `2.5.3` | `qmd` | Installed by setup if missing or if a different global npm version is present. |
| PyPI | `jsonschema` | `4.25.1` | n/a | Installed into the user Python environment for schema validation. |
| PyPI | `requests` | `2.32.5` | n/a | Installed into the user Python environment for helper scripts. |

## Optional Setup Packages

| Ecosystem | Package | Version | Binary | Install Policy |
|---|---:|---:|---|---|
| npm | `dev-browser` | `0.2.8` | `dev-browser` | Installed only when browser acquisition is selected. |
| PyPI | `maigret` | `0.4.4` | `maigret` | Reviewed optional integration. Not auto-installed by default setup. |

## Runtime CLI Packages

These are installed only when the setup choices require that runtime and the binary is missing or at a different npm global version.

| Runtime | Ecosystem | Package | Version | Binary |
|---|---|---:|---:|---|
| Claude Code | npm | `@anthropic-ai/claude-code` | `2.1.169` | `claude` |
| Gemini CLI | npm | `@google/gemini-cli` | `0.45.2` | `gemini` |
| Codex CLI | npm | `@openai/codex` | `0.138.0` | `codex` |
| OpenCode | npm | `opencode-ai` | `1.16.2` | `opencode` |
| Pi | npm | `@mariozechner/pi-coding-agent` | `0.73.1` | `pi` |

## Boundary

Homebrew-managed system prerequisites such as `git`, `node`, `python3`, `jq`, `ollama`, `llama.cpp`, `opencode`, `opencode-desktop`, and Obsidian are not version-pinned by Spotlight. Treat them as host/runtime prerequisites, not Spotlight-reviewed npm/PyPI packages. For higher-assurance deployments, preinstall those system tools through the newsroom's normal device management process and then run setup; Spotlight will still enforce exact npm/PyPI pins for the packages above.
