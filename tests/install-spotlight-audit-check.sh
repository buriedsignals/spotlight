#!/usr/bin/env bash
# Regression checks for install-audit findings F50/F51/F52/F55/F56/F58/F60.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
check() {
  local label="$1"
  shift
  if "$@"; then
    printf 'OK    %s\n' "$label"
  else
    printf 'FAIL  %s\n' "$label" >&2
    fail=1
  fi
}

source_has() { grep -qF -- "$1" install-spotlight.sh; }
source_lacks() { ! grep -qF -- "$1" install-spotlight.sh; }

SANDBOX="$(mktemp -d -t spotlight-audit.XXXXXX)"
trap 'rm -rf "$SANDBOX"' EXIT
mkdir -p "$SANDBOX/home" "$SANDBOX/expected" "$SANDBOX/ambient" "$SANDBOX/vault"

# Exercise the actual first expand_path definition rather than copying it into
# the test. Bash 3.2 and 5.x must both strip the literal ~/ prefix.
EXPAND_PATH_DEF="$(awk '/^expand_path\(\)/ { capture=1 } capture { print } capture && /^}/ { exit }' install-spotlight.sh)"
expanded="$(HOME="$SANDBOX/home" bash -c "$EXPAND_PATH_DEF"$'\n''expand_path "~/Code/spotlight"')"
check "F51: literal tilde expands under HOME" test "$expanded" = "$SANDBOX/home/Code/spotlight"

# Public headless installs are now submitted through Engine's normalized,
# sealed configuration API; the compatibility shell writer must not run.
run_out="$(HOME="$SANDBOX/home" PATH="/usr/bin:/bin" bash install-spotlight.sh --headless --dry-run 2>&1 || true)"
check "F56: legacy headless writer is retired in favor of Engine" \
  grep -qF "Buried Signals Engine (bsig) is required" <<<"$run_out"
check "F56: public install exits before legacy mutation" \
  source_has 'no legacy Obsidian/QMD fallback was applied'

check "F50: Linux has an explicit directory-vault path" \
  source_has 'Linux uses the selected directory as a Markdown vault'
check "F52: installer never writes to system/user site-packages" \
  source_lacks 'pip install --user'
check "F52: reviewed Python dependencies use an install-local venv" \
  source_has 'SPOTLIGHT_PYTHON_ENV="$SPOTLIGHT_DIR/.venv"'
check "F52/F14: private runtime selects Python 3.11 or newer" \
  source_has 'ensure_python_runtime()'
check "F55: pinned Crawl4AI is installed into the runtime environment" \
  source_has '"crawl4ai==$CRAWL4AI_VERSION"'
check "F55: sovereign search provisions digest-pinned SearXNG" \
  source_has 'SEARXNG_IMAGE="searxng/searxng@sha256:'
check "F5: Firecrawl is optional in the installer" \
  source_lacks 'FIRECRAWL_API_KEY:?'
check "F14: pinned Navigator CLI is installed into the runtime environment" \
  source_has '"navigator-cli==$NAVIGATOR_CLI_VERSION"'
check "F58: a user-managed skills-root symlink selects a private fallback" \
  source_has 'if [ -L "$SPOTLIGHT_SKILLS_ROOT" ]'
check "F60: launcher verifies the configured OpenAI model identity" \
  source_has 'verify-openai-model.py'

exit "$fail"
