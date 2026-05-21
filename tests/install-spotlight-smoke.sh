#!/usr/bin/env bash
# Smoke test: run install-spotlight.sh --dry-run against four fixture configs
# covering the cartesian product the installer must support. Assertions
# check that each combo prints the right key install actions without
# touching the filesystem or running brew/npm/curl.
#
# Usage: bash tests/install-spotlight-smoke.sh

set -euo pipefail

cd "$(dirname "$0")/.."
INSTALLER="$(pwd)/install-spotlight.sh"
[ -f "$INSTALLER" ] || { echo "install-spotlight.sh not found at $INSTALLER" >&2; exit 1; }

PASS=0
FAIL=0

mkb64() {
  # Args: KEY=VAL ... ; emits base64 of `export K='V'` lines.
  python3 -c "
import base64, sys, shlex
fields = sys.argv[1:]
out = []
for f in fields:
    k, _, v = f.partition('=')
    out.append(f\"export {k}={shlex.quote(v)}\")
print(base64.b64encode('\n'.join(out).encode('utf-8')).decode('ascii'))
" "$@"
}

check_combo() {
  local label="$1"; shift
  local b64; b64=$(mkb64 "$@")
  local out
  if ! out=$(SPOTLIGHT_CONFIG="$b64" bash "$INSTALLER" --dry-run 2>&1); then
    echo "✗ $label  installer exited non-zero"
    echo "$out" | tail -10 | sed 's/^/    /'
    FAIL=$((FAIL + 1))
    return
  fi
  # Run the per-combo assertions passed via global ASSERTIONS array.
  local missing=()
  for needle in "${ASSERTIONS[@]}"; do
    if ! printf '%s' "$out" | grep -qF "$needle"; then
      missing+=("$needle")
    fi
  done
  if [ ${#missing[@]} -ne 0 ]; then
    echo "✗ $label  missing expected output:"
    for m in "${missing[@]}"; do echo "    - $m"; done
    FAIL=$((FAIL + 1))
  else
    echo "✓ $label  ${#ASSERTIONS[@]} assertions matched"
    PASS=$((PASS + 1))
  fi
}

# Common base config — every combo needs these keys.
BASE=(
  SPOTLIGHT_DIR_INPUT='~/Code/spotlight-test'
  SPOTLIGHT_VAULT_INPUT='~/Vaults/spotlight-test'
  SPOTLIGHT_VAULT_APP='obsidian'
  SPOTLIGHT_INT_BROWSERUSE='false'
  SPOTLIGHT_INT_JUNKIPEDIA='false'
  SPOTLIGHT_INT_UNPAYWALL='false'
  FIRECRAWL_API_KEY='fc-test'
  OSINT_NAV_API_KEY='nav-test'
)

# --- 1. cloud / claude ---
ASSERTIONS=(
  "━━ Prerequisites ━━"
  "━━ Claude Code ━━"
  "AGENTS.md linked as CLAUDE.md"
  "DRY-RUN: write .env"
  "DRY-RUN: write spotlight-doctor"
  "Spotlight installed"
)
check_combo "cloud/claude" "${BASE[@]}" \
  SPOTLIGHT_MODE=cloud SPOTLIGHT_RUNTIME=claude

# --- 2. cloud / opencode-openrouter ---
ASSERTIONS=(
  "━━ OpenCode (provider: openrouter) ━━"
  "DRY-RUN: symlink"
  ".config/opencode/skills/"
  "DRY-RUN: write .env"
  "Spotlight installed"
)
check_combo "cloud/opencode-openrouter" "${BASE[@]}" \
  SPOTLIGHT_MODE=cloud SPOTLIGHT_RUNTIME=opencode \
  SPOTLIGHT_OPENCODE_PROVIDER=openrouter \
  SPOTLIGHT_CLOUD_KEY_VAR=OPENROUTER_API_KEY \
  SPOTLIGHT_CLOUD_KEY=sk-or-test

# --- 3. local / llamacpp / opencode ---
ASSERTIONS=(
  "━━ Local inference (llama-server) ━━"
  "━━ Agent harness (opencode CLI) ━━"
  "DRY-RUN: merge llamacpp provider into"
  "127.0.0.1:8080/v1"
  "DRY-RUN: write ~/.local/bin/spotlight-local for llamacpp/opencode"
  "Spotlight installed"
)
check_combo "local/llamacpp/opencode" "${BASE[@]}" \
  SPOTLIGHT_MODE=local SPOTLIGHT_RUNTIME=local \
  SPOTLIGHT_LOCAL_SERVER=llamacpp SPOTLIGHT_LOCAL_MODEL=qwen27b \
  SPOTLIGHT_AGENT=opencode SPOTLIGHT_OPENCODE_INTERFACE=cli \
  SPOTLIGHT_MODEL_REPO='HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive'

# --- 4. local / llamacpp / pi ---
# The Pi branch prompts interactively (read -r ans </dev/tty), so we feed "y"
# on stdin via process substitution to confirm proceeding past the warning.
# But under --dry-run the prompt is skipped. Verify the warning still prints.
ASSERTIONS=(
  "━━ Local inference (llama-server) ━━"
  "Pi has no native sub-agents."
  "weakening the verification independence guarantee"
  "━━ Agent harness (Pi) ━━"
  "DRY-RUN: write"
  "spotlight-local for llamacpp/pi"
  "Spotlight installed"
)
check_combo "local/llamacpp/pi" "${BASE[@]}" \
  SPOTLIGHT_MODE=local SPOTLIGHT_RUNTIME=local \
  SPOTLIGHT_LOCAL_SERVER=llamacpp SPOTLIGHT_LOCAL_MODEL=gemma \
  SPOTLIGHT_AGENT=pi SPOTLIGHT_OPENCODE_INTERFACE=cli \
  SPOTLIGHT_MODEL_REPO='unsloth/gemma-4-26B-A4B-it-GGUF'

# --- Bonus: missing-config rejection ---
if SPOTLIGHT_CONFIG="" bash "$INSTALLER" --dry-run 2>/dev/null; then
  echo "✗ empty-config rejection  installer should fail when SPOTLIGHT_CONFIG unset"
  FAIL=$((FAIL + 1))
else
  echo "✓ empty-config rejection  installer rejects missing SPOTLIGHT_CONFIG"
  PASS=$((PASS + 1))
fi

echo ""
echo "$PASS passed, $FAIL failed"
exit "$FAIL"
