#!/usr/bin/env bash
# Build the real Flue harness with the pinned @flue/cli: the only CI proof
# that harness/flue compiles and emits its Node server entry point.
set -euo pipefail
cd "$(dirname "$0")/.."

flue="harness/flue/node_modules/.bin/flue"
if [ ! -x "$flue" ]; then
  echo "Flue dependencies are absent; run npm ci in harness/flue first" >&2
  exit 1
fi

out=$(mktemp -d "${TMPDIR:-/tmp}/spotlight-flue-build.XXXXXX")
trap 'rm -rf "$out"' EXIT
(cd harness/flue && ./node_modules/.bin/flue build --target node --output "$out/dist") >/dev/null
[ -f "$out/dist/server.mjs" ] || { echo "Flue build did not emit server.mjs" >&2; exit 1; }
echo "Flue build check passed"
