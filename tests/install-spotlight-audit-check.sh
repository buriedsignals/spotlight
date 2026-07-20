#!/usr/bin/env bash
# Regression checks for the public signed-bootstrap boundary.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
check() { local label="$1"; shift; if "$@"; then printf 'OK    %s\n' "$label"; else printf 'FAIL  %s\n' "$label" >&2; fail=1; fi; }
has() { grep -qF -- "$1" install-spotlight.sh; }
lacks() { ! grep -qF -- "$1" install-spotlight.sh; }

check "signed Engine bootstrap is mandatory" has 'bootstrap_engine || exit 1'
check "Minisign verifies the selected archive" has 'minisign -Vm "$archive" -P "$ENGINE_PUBLIC_KEY"'
check "release metadata is not trusted for integrity" has 'minisign -Vm "$archive" -P "$ENGINE_PUBLIC_KEY"'
check "bootstrap metadata is public" has '$NAVIGATOR_URL/api/artifacts/bootstrap/bsig/$platform'
check "bash does not start Navigator auth" lacks '/auth/cli/start'
check "sealed plan is applied by its recorded binary" has '"$ENGINE_BINARY" apply "$ENGINE_PLAN_PATH"'
check "successful install opens Spotlight onboarding" has '"$ENGINE_BINARY" welcome spotlight'
check "legacy writer is absent" lacks 'ensure_npm_global_exact'
check "Obsidian/QMD writer is absent" lacks 'install_obsidian'
check "shell never sees product credentials" lacks 'FIRECRAWL_API_KEY'

exit "$fail"
