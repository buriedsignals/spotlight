#!/usr/bin/env bash
# Public installer boundary checks. Product matrices now belong to Engine's
# sealed planner suite; this compatibility shell must never execute them.
set -euo pipefail
cd "$(dirname "$0")/.."

pass=0
fail=0
check() {
  local label="$1" expected="$2"; shift 2
  local out rc
  out=$("$@" 2>&1) && rc=0 || rc=$?
  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF "$expected"; then
    printf '✓ %s\n' "$label"
    pass=$((pass + 1))
  else
    printf '✗ %s (rc=%s)\n%s\n' "$label" "$rc" "$out" >&2
    fail=$((fail + 1))
  fi
}

tmp="$(mktemp -d -t spotlight-public-install.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT

check "missing Engine fails before mutation" \
  "Buried Signals Engine (bsig) is required" \
  env HOME="$tmp/home" PATH="/usr/bin:/bin" bash install-spotlight.sh --dry-run

mkdir -p "$tmp/bin"
printf '#!/usr/bin/env bash\nexit 0\n' > "$tmp/bin/bsig"
chmod +x "$tmp/bin/bsig"
check "legacy headless writer is retired" \
  "The legacy headless installer is retired" \
  env HOME="$tmp/home" PATH="$tmp/bin:/usr/bin:/bin" bash install-spotlight.sh --headless --dry-run

check "retired base64 channel still fails loud" \
  "no longer accepts SPOTLIGHT_CONFIG" \
  env SPOTLIGHT_CONFIG=x HOME="$tmp/home" PATH="$tmp/bin:/usr/bin:/bin" bash install-spotlight.sh --dry-run

printf '%s passed, %s failed\n' "$pass" "$fail"
exit "$fail"
