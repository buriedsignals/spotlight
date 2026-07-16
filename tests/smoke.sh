#!/usr/bin/env bash
# Spotlight smoke test — exercises the install contract without spending any API calls.
#
# Checks:
#   1. All 17 skill directories present with SKILL.md
#   2. All 2 agent prompts present
#   3. All schemas parse as valid JSON
#   4. Integrations preflight runs cleanly
#   5. Monitoring registry helper runs cleanly
#   6. RLM helper and flow proxy run without requiring Ollama
#   7. No banned Claude-specific syntax in skills/agents
#   8. No legacy local feed framework remains
#   9. AGENTS.md skill registry matches skills-manifest.json count
#  10. Integration routing rows resolve
#  11. setup.html exists
#  12. index.html exists
#  13. DISCLAIMER.md + LICENSE present
#  14. Setup dependency pins are enforced
#  15. Configurator server contract holds
#  16. Installer + landing-page fragment checks pass
#  17. Installer dry-run matrix passes (4 combos + contract checks)
#
# Exit 0 on pass, 1 if any check fails.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
FAIL=0

_c_green=$'\033[32m'; _c_red=$'\033[31m'; _c_reset=$'\033[0m'; _c_dim=$'\033[2m'

ok() { printf "%s✓%s %s\n" "$_c_green" "$_c_reset" "$1"; PASS=$((PASS+1)); }
fail() { printf "%s✗%s %s%s\n" "$_c_red" "$_c_reset" "$1" "${2:+ ${_c_dim}— $2${_c_reset}}"; FAIL=$((FAIL+1)); }

cd "$ROOT"

echo "── Structure ──"

expected_skills=(spotlight review integrations ingest report-drafting monitoring provenance-signing acquisition-graduation web-archiving content-access epistemic-grounding shell-safety osint investigate follow-the-money social-media-intelligence technical-investigation)
for skill in "${expected_skills[@]}"; do
  if [ -f "skills/$skill/SKILL.md" ]; then
    ok "skills/$skill/SKILL.md present"
  else
    fail "skills/$skill/SKILL.md missing"
  fi
done

for agent in investigator fact-checker; do
  if [ -f "agents/$agent.md" ]; then
    ok "agents/$agent.md present"
  else
    fail "agents/$agent.md missing"
  fi
done

echo ""
echo "── Schemas ──"
for s in findings fact-check report-draft methodology investigation-log summary evidence-bundle provenance-manifest rlm-analysis source-expressions case-contract; do
  if [ -f "schemas/$s.schema.json" ]; then
    if python3 -c "import json; json.load(open('schemas/$s.schema.json'))" 2>/dev/null; then
      ok "schemas/$s.schema.json parses"
    else
      fail "schemas/$s.schema.json malformed"
    fi
  else
    fail "schemas/$s.schema.json missing"
  fi
done

echo ""
echo "── Preflight scripts ──"
python3 integrations/preflight.py --text >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ] || [ $rc -eq 1 ]; then
  ok "integrations/preflight.py runs (rc=$rc)"
else
  fail "integrations/preflight.py failed with rc=$rc"
fi

python3 monitoring/registry.py schema --json >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  ok "monitoring/registry.py runs"
else
  fail "monitoring/registry.py failed with rc=$rc"
fi

echo ""
echo "── RLM ──"
python3 tests/rlm-helper-check.py >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  ok "tests/rlm-helper-check.py runs"
else
  fail "tests/rlm-helper-check.py failed with rc=$rc"
fi

python3 tests/rlm-flow-check.py >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  ok "tests/rlm-flow-check.py runs"
else
  fail "tests/rlm-flow-check.py failed with rc=$rc"
fi

python3 tests/rlm-methodology-contract-check.py >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  ok "RLM methodology contract documented"
else
  fail "RLM methodology contract check failed with rc=$rc"
fi

echo ""
echo "── Vault claims layer ──"
python3 tests/vault-claims-check.py >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  ok "tests/vault-claims-check.py passes (fixture + negative self-tests)"
else
  fail "tests/vault-claims-check.py failed with rc=$rc"
fi

echo ""
echo "── Validators and helpers ──"
for t in validate-case-check validate-fact-check-check source-expression-producer-contract-check migrate-source-expressions-check validate-report-check render-report-check provenance-manifest-check monitoring-registry-check preflight-check cti-upstream-check technical-investigation-check verified-indicator-export-check; do
  python3 "tests/$t.py" >/dev/null 2>&1
  rc=$?
  if [ $rc -eq 0 ]; then
    ok "tests/$t.py passes"
  else
    fail "tests/$t.py failed with rc=$rc"
  fi
done

echo ""
echo "── Cleanliness ──"

banned_syntax=$(grep -rlE 'WebFetch|WebSearch|allowedTools|disallowedTools|maxTurns|run_in_background' skills/ agents/ 2>/dev/null || true)
if [ -z "$banned_syntax" ]; then
  ok "no banned Claude-specific syntax in skills/ agents/"
else
  fail "banned syntax found in: $banned_syntax"
fi

if [ ! -d "monitoring/feeds" ]; then
  ok "legacy monitoring/feeds framework removed"
else
  fail "legacy monitoring/feeds framework still present"
fi

echo ""
echo "── Contracts ──"
skill_count=$(grep -cE '^\| `[a-z-]+` \| `skills/' AGENTS.md || echo 0)
manifest_count=$(python3 - <<'PY'
import json
print(len(json.load(open("skills-manifest.json"))["skills"]))
PY
)
if [ "$skill_count" = "$manifest_count" ]; then
  ok "AGENTS.md skill registry has $skill_count entries"
else
  fail "AGENTS.md skill registry count off: got $skill_count, want $manifest_count"
fi

python3 tests/integrations-routing-check.py >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  ok "integration routing rows resolve"
else
  fail "integration routing rows drifted"
fi

python3 tests/dependency-pins-check.py >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  ok "setup dependency pins enforced"
else
  fail "setup dependency pins drifted"
fi

python3 tests/plugin-distribution-check.py >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  ok "plugin distribution payload valid"
else
  fail "plugin distribution payload drifted"
fi

echo ""
echo "── Installer ──"
python3 tests/configurator-server-check.py >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  ok "configurator server contract holds"
else
  fail "configurator server check failed with rc=$rc"
fi

bash tests/install-spotlight-check.sh >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  ok "installer + landing-page fragments hold"
else
  fail "installer fragment check failed with rc=$rc"
fi

bash tests/install-spotlight-smoke.sh >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  ok "installer dry-run matrix passes"
else
  fail "installer dry-run matrix failed with rc=$rc"
fi

bash tests/install-spotlight-audit-check.sh >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  ok "install-audit regressions hold"
else
  fail "install-audit regression check failed with rc=$rc"
fi

python3 tests/local-model-identity-check.py >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  ok "local model identity contract holds"
else
  fail "local model identity check failed with rc=$rc"
fi

echo ""
echo "── Entry points ──"
for f in setup.html index.html DISCLAIMER.md LICENSE VALIDATED_DEPENDENCIES.md; do
  if [ -f "$f" ]; then
    ok "$f present"
  else
    fail "$f missing"
  fi
done

echo ""
if [ $FAIL -eq 0 ]; then
  printf "%s✓ All %d checks passed%s\n" "$_c_green" "$PASS" "$_c_reset"
  exit 0
else
  printf "%s✗ %d failed / %d passed%s\n" "$_c_red" "$FAIL" "$PASS" "$_c_reset"
  exit 1
fi
