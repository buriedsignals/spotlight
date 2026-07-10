#!/usr/bin/env bash
# Static checks for the canonical installer (install-spotlight.sh) and the
# pages around it. Replaces the old setup-generator-check.js assertions:
# the installer is one static, reviewable file and the hosted setup.html is
# a key-free landing page, so we lint both directly instead of
# string-building scripts in JS.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
note() { printf 'FAIL  %s\n' "$1"; fail=1; }

bash -n install-spotlight.sh || { echo "install-spotlight.sh does not parse"; exit 1; }

includes() {
  grep -qF -- "$2" "$1" || note "$1 missing fragment: $2"
}
excludes() {
  if grep -qF -- "$2" "$1"; then note "$1 stale fragment present: $2"; fi
}

# ── install-spotlight.sh: configurator head contract ──
# Configurator phase: local server collects config, installer sources artifacts
includes install-spotlight.sh 'python3 "$CONFIGURATOR_DIR/install/setup_server.py" --profile-dir "$SPOTLIGHT_PROFILE_DIR" --repo-dir "$CONFIGURATOR_DIR"'
# Double gate: server exit code alone is not trusted — artifacts must exist
includes install-spotlight.sh 'if [ ! -f "$SETUP_CONFIG" ] || [ ! -f "$STAGED_ENV" ]; then'
# Version handshake with install/setup_server.py + install/configure.html
includes install-spotlight.sh 'CONFIGURATOR_VERSION="1"'
# Staged secrets never persist in two places, and never orphan on abort
includes install-spotlight.sh 'rm -f "$STAGED_ENV"'
includes install-spotlight.sh 'trap cleanup_staged_env EXIT'
# Retired SPOTLIGHT_CONFIG channel fails loud
includes install-spotlight.sh 'no longer accepts SPOTLIGHT_CONFIG'
# Headless / CI path
includes install-spotlight.sh '--headless) HEADLESS=1 ;;'
# dev-browser installs through the reviewed-pin path
includes install-spotlight.sh 'ensure_npm_global_exact dev-browser dev-browser'
# Doctor/updater/launcher heredocs bake the unexpanded input literal
includes install-spotlight.sh "SPOTLIGHT_DIR_DEFAULT_INPUT='\$SPOTLIGHT_DIR_INPUT'"
includes install-spotlight.sh 'Runtime case contract: use the exact project slug'
includes install-spotlight.sh 'scripts/finalize-report.py" "\$CASE_DIR" --if-ready'
includes install-spotlight.sh 'data/report-draft.json'
includes install-spotlight.sh 'Frontier CLI exit backstop (not per-response middleware)'
includes install-spotlight.sh 'finalize-report.py" "\$active_case" --if-ready'
includes install-spotlight.sh '*[!A-Za-z0-9._-]*'
# No blob/eval head remnants
excludes install-spotlight.sh 'base64 -d'
excludes install-spotlight.sh "SPOTLIGHT_CONFIG='"
excludes install-spotlight.sh 'eval "$(printf'
excludes install-spotlight.sh 'SPOTLIGHT_INT_BROWSERUSE'

# ── setup.html: static key-free landing page ──
# Advertises the canonical one-liner and the key-free ZIP bootstrap
includes setup.html 'curl -fsSL https://spotlight.buriedsignals.com/install-spotlight.sh | bash'
includes setup.html 'spotlight-install.command'
includes setup.html 'curl -fsSL https://spotlight.buriedsignals.com/install-spotlight.sh -o'
# Zero form fields, zero generator machinery, zero retired config channel
excludes setup.html '<input'
excludes setup.html 'SPOTLIGHT_CONFIG'
excludes setup.html 'buildExportBlock'

# ── install/configure.html: local configurator page ──
includes install/configure.html '<meta name="configurator-version" content="1">'
includes install/configure.html '__SETUP_TOKEN__'
# The installer is the pin authority — the configurator carries no @x.y.z pins
if grep -qE -- '@[0-9]+\.[0-9]+\.[0-9]+' install/configure.html; then
  note 'install/configure.html carries an npm version pin (@x.y.z) — pins live in install-spotlight.sh only'
fi

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -S error install-spotlight.sh || fail=1
fi

[ "$fail" = "0" ] && echo "install-spotlight.sh checks passed" || exit 1
