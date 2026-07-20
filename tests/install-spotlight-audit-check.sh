#!/usr/bin/env bash
# Regression checks for the public/member boundary.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
check() { local label="$1"; shift; if "$@"; then printf 'OK    %s\n' "$label"; else printf 'FAIL  %s\n' "$label" >&2; fail=1; fi; }
has() { grep -qF -- "$1" "$2"; }
lacks() { ! grep -qF -- "$1" "$2"; }

check "public installer does not bootstrap Engine" lacks 'bootstrap_engine' install-spotlight.sh
check "public installer never invokes bsig" lacks '"$ENGINE_BINARY"' install-spotlight.sh
check "public installer contains no Engine bridge asset" lacks 'engine_bridge.py' install-spotlight.sh
check "public installer contains no Engine binary locator" lacks 'BSIG_BIN' install-spotlight.sh
check "public installer contains no Engine repository URL" lacks 'buriedsignals/engine' install-spotlight.sh
check "public installer keeps canonical install body" has 'step "Spotlight repo"' install-spotlight.sh
check "public configurator has direct member connection" has 'Yes, authenticate' install/configure.html
check "public configurator has explicit skip" has 'Continue without Navigator' install/configure.html
check "non-members do not receive Navigator CLI" lacks 'navigator-cli==' install-spotlight.sh
check "Navigator skill remains in manifest" has 'navigator' skills.manifest
check "Data Navigator is identified as Lab-only" has 'Data Navigator requires Lab' install/configure.html

exit "$fail"
