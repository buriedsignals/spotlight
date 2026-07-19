#!/usr/bin/env bash
# Static contract checks for the intentionally small public bootstrap.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
note() { printf 'FAIL  %s\n' "$1"; fail=1; }
includes() { grep -qF -- "$2" "$1" || note "$1 missing fragment: $2"; }
excludes() { if grep -qF -- "$2" "$1"; then note "$1 stale fragment present: $2"; fi; }

bash -n install-spotlight.sh || { echo "install-spotlight.sh does not parse"; exit 1; }

includes install-spotlight.sh 'bootstrap_engine || exit 1'
includes install-spotlight.sh 'python3 "$TMP_ASSETS/install/setup_server.py" --profile-dir "$SPOTLIGHT_PROFILE_DIR" --repo-dir "$TMP_ASSETS"'
includes install-spotlight.sh "ENGINE_PUBLIC_KEY='RWRVGhTzAGx7pqB8NEMCPW8uMr10Koa3wSoIH9OCqoCkL4GUqhQcwtU6'"
includes install-spotlight.sh 'minisign -Vm "$archive" -P "$ENGINE_PUBLIC_KEY"'
includes install-spotlight.sh '"$ENGINE_BINARY" apply "$ENGINE_PLAN_PATH"'
includes install-spotlight.sh '"$ENGINE_BINARY" welcome spotlight'
includes install-spotlight.sh 'no legacy Obsidian/QMD fallback was applied'
includes install-spotlight.sh 'CONFIGURATOR_VERSION="1"'
includes install-spotlight.sh 'no longer accepts SPOTLIGHT_CONFIG'
excludes install-spotlight.sh 'base64 -d'
excludes install-spotlight.sh 'qmd'
excludes install-spotlight.sh 'obsidian'

includes install/setup_server.py 'json.dump(response, handle)'
includes install/engine_bridge.py '"engine_binary": self.binary'
includes install/configure.html '<meta name="configurator-version" content="1">'
includes install/configure.html '__SETUP_TOKEN__'

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -S error install-spotlight.sh || fail=1
fi

[ "$fail" = "0" ] && echo "install-spotlight.sh checks passed" || exit 1
