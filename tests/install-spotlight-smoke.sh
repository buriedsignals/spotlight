#!/usr/bin/env bash
# Fail-closed pointer checks for the retired public installer.
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

check "install pointer sends journalists to Indicator Labs" \
  "https://buriedsignals.com/join" \
  env HOME="$tmp/home" PATH="/usr/bin:/bin" /bin/bash install-spotlight.sh

check "retired base64 channel still fails loud" \
  "no longer accepts SPOTLIGHT_CONFIG" \
  env SPOTLIGHT_CONFIG=x HOME="$tmp/home" PATH="/usr/bin:/bin" bash install-spotlight.sh

echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
