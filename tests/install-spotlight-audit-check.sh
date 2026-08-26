#!/usr/bin/env bash
# Regression checks for the public/member boundary after configurator retirement.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
check() { local label="$1"; shift; if "$@"; then printf 'OK    %s\n' "$label"; else printf 'FAIL  %s\n' "$label" >&2; fail=1; fi; }
has() { grep -qF -- "$1" "$2"; }
lacks() { ! grep -qF -- "$1" "$2"; }
missing() { [ ! -e "$1" ]; }

check "install pointer does not bootstrap Engine" lacks 'bootstrap_engine' install-spotlight.sh
check "install pointer never invokes bsig" lacks '"$ENGINE_BINARY"' install-spotlight.sh
check "install pointer contains no Engine bridge asset" lacks 'engine_bridge.py' install-spotlight.sh
check "install pointer contains no Engine binary locator" lacks 'BSIG_BIN' install-spotlight.sh
check "install pointer contains no Engine repository URL" lacks 'buriedsignals/engine' install-spotlight.sh
check "install pointer sends journalists to Indicator Labs" has 'https://buriedsignals.com/join' install-spotlight.sh
check "localhost configurator HTML is gone" missing install/configure.html
check "localhost configurator server is gone" missing install/setup_server.py
check "non-members do not receive Navigator CLI" lacks 'navigator-cli==' install-spotlight.sh
check "Navigator skill remains in manifest" has 'navigator' skills.manifest
check "Spotlight landing does not advertise Scoutpost" lacks 'Scoutpost' index.html

exit "$fail"
