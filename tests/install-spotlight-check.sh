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

excludes index.html 'Scoutpost'
excludes index.html 'Splash'
includes skills/navigator/SKILL.md 'OSINT tool discovery'
includes scripts/navigator-connect 'NavigatorInstallerBridge'
includes scripts/navigator-connect 'selected_runtime(args.runtime)'
includes install/navigator_bridge.py '"local": "pi-flue"'
includes install/navigator_bridge.py '"codex": "codex-cli"'

if [ -e install/setup_server.py ]; then note "install/setup_server.py must be deleted"; fi
if [ -e install/engine_bridge.py ]; then note "install/engine_bridge.py must be deleted"; fi

# Old curl|bash pipes must fail closed: run the pointer with a PATH that holds
# only `cat`, so any other external command it tried would fail as not found.
pointer_tmp="$(mktemp -d)"
trap 'rm -rf "$pointer_tmp"' EXIT
mkdir "$pointer_tmp/bin"
ln -s "$(command -v cat)" "$pointer_tmp/bin/cat"
pointer_rc=0
env -i HOME="$pointer_tmp" PATH="$pointer_tmp/bin" "$BASH" install-spotlight.sh >"$pointer_tmp/out" 2>&1 || pointer_rc=$?
[ "$pointer_rc" = "1" ] || note "install-spotlight.sh must exit 1 using only cat (rc=$pointer_rc)"
if grep -qF 'command not found' "$pointer_tmp/out"; then note "install-spotlight.sh ran a command other than cat"; fi
for line in 'it does not install Spotlight' 'install with Indicator Labs at https://buriedsignals.com/join' "follow README.md's signed Engine instructions"; do
  grep -qF -- "$line" "$pointer_tmp/out" || note "install-spotlight.sh output missing: $line"
done

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -S error install-spotlight.sh || fail=1
fi

[ "$fail" = "0" ] && echo "install-spotlight.sh checks passed" || exit 1
