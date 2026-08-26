#!/usr/bin/env bash
# Static contract checks after the localhost configurator was retired.
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

includes install-spotlight.sh 'https://buriedsignals.com/join'
includes install-spotlight.sh 'Indicator Labs'
includes install-spotlight.sh 'There is no localhost configure.html server'
includes install-spotlight.sh 'no longer accepts SPOTLIGHT_CONFIG'
excludes install-spotlight.sh 'setup_server.py'
excludes install-spotlight.sh 'engine_bridge.py'
excludes install-spotlight.sh 'bootstrap_engine'
excludes install-spotlight.sh 'minisign -Vm'
excludes install-spotlight.sh 'navigator-cli=='
excludes install-spotlight.sh 'base64 -d'
excludes install-spotlight.sh '@tobilu/qmd'
excludes install-spotlight.sh 'SPOTLIGHT_VAULT_APP'
excludes harness/flue/src/agents/spotlight.ts 'SPOTLIGHT_KNOWLEDGE_ROOT'
includes harness/flue/src/lib/roles.ts '--config ${HARNESS_ROOT}/.spotlight-config.json --case-dir <CASE_DIR>'
includes harness/flue/src/lib/roles.ts 'scripts/query_vault.py'
excludes harness/flue/src/agents/spotlight.ts "connectMcpServer('openknowledge'"
excludes harness/flue/src/agents/spotlight.ts 'OPEN_KNOWLEDGE_TOOLS'
excludes index.html 'Scoutpost'
excludes index.html 'Splash'
includes skills/navigator/SKILL.md 'OSINT tool discovery'
includes scripts/navigator-connect 'NavigatorInstallerBridge'
includes scripts/navigator-connect 'selected_runtime(args.runtime)'
includes install/navigator_bridge.py '"local": "pi-flue"'
includes install/navigator_bridge.py '"codex": "codex-cli"'
excludes scripts/navigator-connect 'NavigatorInstallerBridge(ROOT / "install" / "navigator-transport-matrix.json", "claude-code")'
excludes scripts/navigator-connect 'from setup_server import'

if [ -e install/configure.html ]; then note "install/configure.html must be deleted"; fi
if [ -e install/setup_server.py ]; then note "install/setup_server.py must be deleted"; fi
if [ -e install/engine_bridge.py ]; then note "install/engine_bridge.py must be deleted"; fi
if [ -e setup.html ]; then note "setup.html must be deleted"; fi

if grep -qiF obsidian install-spotlight.sh; then note "install-spotlight.sh stale fragment present: obsidian"; fi
if grep -qiF tolaria install-spotlight.sh; then note "install-spotlight.sh stale fragment present: tolaria"; fi

if bash install-spotlight.sh >/tmp/spotlight-install-pointer.out 2>&1; then
  note "install-spotlight.sh must exit non-zero so old curl|bash pipes fail closed"
fi
if ! grep -qF 'https://buriedsignals.com/join' /tmp/spotlight-install-pointer.out; then
  note "install-spotlight.sh output missing Indicator Labs join URL"
fi
rm -f /tmp/spotlight-install-pointer.out

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -S error install-spotlight.sh || fail=1
fi

[ "$fail" = "0" ] && echo "install-spotlight.sh checks passed" || exit 1
