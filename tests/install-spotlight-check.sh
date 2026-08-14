#!/usr/bin/env bash
# Static contract checks for the public Spotlight installer.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
note() { printf 'FAIL  %s\n' "$1"; fail=1; }
includes() { grep -qF -- "$2" "$1" || note "$1 missing fragment: $2"; }
excludes() { if grep -qF -- "$2" "$1"; then note "$1 stale fragment present: $2"; fi; }

bash -n install-spotlight.sh || { echo "install-spotlight.sh does not parse"; exit 1; }
bash -n scripts/spotlight-uninstall || { echo "scripts/spotlight-uninstall does not parse"; exit 1; }
bash tests/spotlight-uninstall-check.sh || { echo "Spotlight uninstall cleanup checks failed"; exit 1; }
[ -x scripts/spotlight-uninstall ] || note "scripts/spotlight-uninstall must be executable so install does not dirty the checkout"
includes .gitignore '.venv/'
includes scripts/spotlight-uninstall 'remove_owned_link "$bin/spotlight-uninstall"'
includes scripts/spotlight-uninstall 'remove_owned_file "$bin/spotlight-doctor"'
includes scripts/spotlight-uninstall 'remove_shell_block "$HOME/.zshrc"'

includes install-spotlight.sh '--legacy-only'
includes install-spotlight.sh 'navigator_bridge.py'
includes install-spotlight.sh 'navigator-transport-matrix.json'
includes install-spotlight.sh 'ensure_npm_global_exact open-knowledge @inkeep/open-knowledge'
includes install-spotlight.sh 'open-knowledge --cwd "$SPOTLIGHT_VAULT_PATH" init --mcp --local-only'
includes install-spotlight.sh 'write_env_var SPOTLIGHT_WORKSPACE_PATH "$SPOTLIGHT_VAULT_PATH"'
includes install-spotlight.sh 'command -v open-knowledge >/dev/null 2>&1 || { echo "OpenKnowledge CLI missing — re-run install-spotlight.sh"'
includes install-spotlight.sh 'project_spotlight_skills_into_workspace'
includes install-spotlight.sh 'export SPOTLIGHT_OPENKNOWLEDGE_MCP_URL="http://127.0.0.1:\$OK_PORT/mcp"'
includes install-spotlight.sh 'open-knowledge --cwd "\$SPOTLIGHT_VAULT_PATH" config validate'
includes harness/flue/src/agents/spotlight.ts "connectMcpServer('openknowledge'"
includes harness/flue/src/agents/spotlight.ts 'tools: OPEN_KNOWLEDGE_TOOLS'
includes install-spotlight.sh 'spotlight-navigator'
includes install-spotlight.sh ': "${SPOTLIGHT_NAVIGATOR_CONNECTION:=locked}"'
includes install-spotlight.sh '[ -n "$OSINT_NAV_API_KEY" ] && write_env_var OSINT_NAV_API_KEY'
includes install-spotlight.sh 'CONFIGURATOR_VERSION="2"'
includes install-spotlight.sh 'no longer accepts SPOTLIGHT_CONFIG'
excludes install-spotlight.sh 'bootstrap_engine'
excludes install-spotlight.sh 'minisign -Vm'
excludes install-spotlight.sh 'navigator-cli=='
excludes install-spotlight.sh 'OSINT_NAV_API_KEY:?'
excludes install-spotlight.sh 'base64 -d'
excludes install-spotlight.sh '@tobilu/qmd'
excludes install-spotlight.sh 'qmd collection'

includes install/setup_server.py 'NavigatorInstallerBridge'
includes install/setup_server.py '"codex": "codex-cli"'
includes install/setup_server.py '"local": "pi-flue"'
includes install/setup_server.py 'navigator_choice not in {"connect", "skip"}'
includes install/configure.html 'Yes, authenticate'
includes install/configure.html 'Continue without Navigator'
includes install/configure.html 'Data Navigator requires Lab'
includes install/configure.html "runtime:navigatorRuntimeChoice()"
excludes install/configure.html 'Splash'
excludes setup.html 'Splash'
excludes index.html 'Scoutpost'
excludes index.html 'Splash'
includes skills/navigator/SKILL.md 'Data Navigator'
includes scripts/navigator-connect 'NavigatorInstallerBridge'
includes scripts/navigator-connect 'selected_runtime(args.runtime)'
excludes scripts/navigator-connect 'NavigatorInstallerBridge(ROOT / "install" / "navigator-transport-matrix.json", "claude-code")'

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -S error install-spotlight.sh || fail=1
fi

[ "$fail" = "0" ] && echo "install-spotlight.sh checks passed" || exit 1
