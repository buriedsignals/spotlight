#!/usr/bin/env bash
# Spotlight smoke test — exercises the install contract without spending any API calls.
#
# Checks:
#   1. All 11 skill directories present with SKILL.md
#   2. All 2 agent prompts present
#   3. All 5 schemas parse as valid JSON
#   4. Monitoring preflight runs cleanly
#   5. Integrations preflight runs cleanly
#   6. No banned Claude-specific syntax in skills/agents
#   7. No coJournalist residue outside docs/plans/
#   8. AGENTS.md has 11 entries in skill registry
#   9. setup.html exists
#  10. index.html exists
#  11. DISCLAIMER.md + LICENSE present
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

expected_skills=(spotlight review integrations ingest monitoring web-archiving content-access osint investigate follow-the-money social-media-intelligence)
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
for s in findings fact-check methodology investigation-log summary; do
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
python3 monitoring/feeds/preflight.py --text >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ] || [ $rc -eq 1 ]; then
  ok "monitoring/feeds/preflight.py runs (rc=$rc)"
else
  fail "monitoring/feeds/preflight.py failed with rc=$rc"
fi

python3 integrations/preflight.py --text >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ] || [ $rc -eq 1 ]; then
  ok "integrations/preflight.py runs (rc=$rc)"
else
  fail "integrations/preflight.py failed with rc=$rc"
fi

echo ""
echo "── Cleanliness ──"

banned_syntax=$(grep -rlE 'WebFetch|WebSearch|allowedTools|disallowedTools|maxTurns|run_in_background' skills/ agents/ 2>/dev/null || true)
if [ -z "$banned_syntax" ]; then
  ok "no banned Claude-specific syntax in skills/ agents/"
else
  fail "banned syntax found in: $banned_syntax"
fi

# coJournalist is now a named deferred integration (per integrations/ framework),
# so mentioning it in skills/integrations/ and docs/integrations.md is expected.
# Check only that it's not treated as an active monitoring source anymore.
monitoring_cojournalist=$(grep -rli cojournalist monitoring/feeds/sources/ 2>/dev/null || true)
if [ -z "$monitoring_cojournalist" ]; then
  ok "no coJournalist as active monitoring source"
else
  fail "coJournalist still in monitoring/feeds/sources/: $monitoring_cojournalist"
fi

echo ""
echo "── Contracts ──"
skill_count=$(grep -cE '^\| `[a-z-]+` \| `skills/' AGENTS.md || echo 0)
if [ "$skill_count" = "11" ]; then
  ok "AGENTS.md skill registry has 11 entries"
else
  fail "AGENTS.md skill registry count off: got $skill_count, want 11"
fi

echo ""
echo "── Entry points ──"
for f in setup.html index.html DISCLAIMER.md LICENSE; do
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
