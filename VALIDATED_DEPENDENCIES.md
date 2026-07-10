# Spotlight Validated Dependencies

Reviewed on: 2026-07-10

This file is the source of truth for packages the Spotlight setup flow may install from npm or PyPI. The setup page and `install-spotlight.sh` must install exact versions only. If a package is not listed here, the installer must stop and report that manual review is required.

## Default Setup Packages

| Ecosystem | Package | Version | Binary | Install Policy |
|---|---:|---:|---|---|
| PyPI (install-local venv) | `crawl4ai` | `0.9.0` | `crwl` | Sovereign scrape default (`fetch`). Installed into Spotlight's `.venv`, followed by `crawl4ai-setup` for the Playwright browser runtime. Reviewed 2026-07-10. |
| PyPI (install-local venv) | `navigator-cli` | `0.1.0` | `navigator` | OSINT Navigator client used by the OSINT skill; isolated with Spotlight's Python runtime. |
| npm | `@tobilu/qmd` | `2.5.3` | `qmd` | Installed by setup if missing or if a different global npm version is present. |
| PyPI | `jsonschema` | `4.25.1` | n/a | Installed into Spotlight's `.venv` for schema validation. |
| PyPI | `requests` | `2.32.5` | n/a | Installed into Spotlight's `.venv` for helper scripts. |

Sovereign `search` is backed by a **local SearXNG** Docker service (JSON API on `SEARXNG_URL`, default `http://localhost:8899`), and the opt-in `--tor` fetch (U7) uses a local **Tor** SOCKS proxy on `9050`. Both are services, not packaged libraries — provisioned by the installer, not this table.

## Optional Setup Packages

| Ecosystem | Package | Version | Binary | Install Policy |
|---|---:|---:|---|---|
| npm | `firecrawl-cli` | `1.9.8` | `firecrawl` | **Optional escape hatch** (KTD4/KTD6): scrape fallback on a hard bot-block + optional search-union. Installed only when the Firecrawl fallback is enabled (`FIRECRAWL_API_KEY` present); absent = pure-sovereign. Matches the signed Engine catalog pin. |
| npm | `dev-browser` | `0.2.8` | `dev-browser` | Installed only when browser acquisition is selected. |
| PyPI | `maigret` | `0.4.4` | `maigret` | Reviewed optional integration. Not auto-installed by default setup. |

## Runtime CLI Packages

These are installed only when the setup choices require that runtime and the binary is missing or at a different npm global version.

| Runtime | Ecosystem | Package | Version | Binary |
|---|---|---:|---:|---|
| Claude Code | npm | `@anthropic-ai/claude-code` | `2.1.169` | `claude` |
| Gemini CLI | npm | `@google/gemini-cli` | `0.45.2` | `gemini` |
| Codex CLI | npm | `@openai/codex` | `0.138.0` | `codex` |
| OpenCode | npm | `opencode-ai` | `1.17.7` | `opencode` |
| Pi | npm | `@earendil-works/pi-coding-agent` | `0.79.6` | `pi` |

## Boundary

Homebrew-managed system prerequisites such as `git`, `node`, `python3`, `jq`, `ollama`, `llama.cpp`, `opencode`, `opencode-desktop`, and Obsidian are not version-pinned by Spotlight. Treat them as host/runtime prerequisites, not Spotlight-reviewed npm/PyPI packages. For higher-assurance deployments, preinstall those system tools through the newsroom's normal device management process and then run setup; Spotlight will still enforce exact npm/PyPI pins for the packages above.
