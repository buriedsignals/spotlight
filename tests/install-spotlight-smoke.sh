#!/usr/bin/env bash
# Public installer boundary checks for the Engine-free shell entry point.
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

check "fresh bootstrap stops before configuration when prerequisites are absent" \
  "python3 is required for the configurator" \
  env -u BSIG_BIN HOME="$tmp/home" PATH="/bin" /bin/bash install-spotlight.sh

mkdir -p "$tmp/bin"
printf '#!/usr/bin/env bash\nexit 0\n' > "$tmp/bin/bsig"
chmod +x "$tmp/bin/bsig"
check "headless path stays product-local and validates its configuration" \
  "install path missing from config" \
  env HOME="$tmp/home" PATH="$tmp/bin:/usr/bin:/bin" bash install-spotlight.sh --headless --dry-run

check "retired base64 channel still fails loud" \
  "no longer accepts SPOTLIGHT_CONFIG" \
  env SPOTLIGHT_CONFIG=x HOME="$tmp/home" PATH="$tmp/bin:/usr/bin:/bin" bash install-spotlight.sh --dry-run

printf '%s passed, %s failed\n' "$pass" "$fail"
exit "$fail"
