#!/usr/bin/env bash
# Build the real Flue harness with an OpenKnowledge-style selected workspace.
# The workspace projects the two shared skills by their flat runtime names,
# exactly as Engine's ok_skill_sync step does for Pi/Flue installations.
set -euo pipefail
cd "$(dirname "$0")/.."

flue="harness/flue/node_modules/.bin/flue"
if [ ! -x "$flue" ]; then
  echo "Flue dependencies are absent; run npm ci in harness/flue first" >&2
  exit 1
fi

workspace=$(mktemp -d "${TMPDIR:-/tmp}/spotlight-ok-workspace.XXXXXX")
trap 'rm -rf "$workspace"' EXIT
mkdir -p "$workspace/.agents/skills"
ln -s "$PWD/skills/epistemic-grounding" "$workspace/.agents/skills/epistemic-grounding"
ln -s "$PWD/skills/shell-safety" "$workspace/.agents/skills/shell-safety"

(cd harness/flue && SPOTLIGHT_CWD="$workspace" ./node_modules/.bin/flue build --target node --output "$workspace/dist") >/dev/null
[ -f "$workspace/dist/server.mjs" ] || { echo "Flue build did not emit server.mjs" >&2; exit 1; }
echo "Flue OpenKnowledge workspace check passed"
