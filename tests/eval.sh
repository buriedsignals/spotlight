#!/usr/bin/env bash
# Spotlight eval — contract compliance + sample data validation.
#
# Goes beyond smoke.sh's structural checks: validates that agent + skill
# frontmatter is well-formed, allowed_verbs reference real verbs from
# AGENTS.md, sample case files validate against their schemas, and the
# RUNTIMES table in setup.html is consistent with the HTML/UI.
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

echo "── Agent contract compliance ──"

for agent in agents/*.md; do
  agent_id=$(basename "$agent" .md)

  # Must have YAML frontmatter with required fields
  if head -30 "$agent" | grep -q "^name: $agent_id" && \
     head -30 "$agent" | grep -q "^iteration_limit:" && \
     head -30 "$agent" | grep -q "^allowed_verbs:"; then
    ok "$agent_id frontmatter has name/iteration_limit/allowed_verbs"
  else
    fail "$agent_id frontmatter missing required fields"
  fi

  # allowed_verbs should all be in the canonical 13-verb list from AGENTS.md
  # Extract the verb list from the agent's frontmatter (between allowed_verbs: and next top-level key)
  agent_verbs=$(awk '/^allowed_verbs:/{flag=1; next} flag && /^  - / {print $2} flag && /^[a-z_]+:/ {exit}' "$agent")
  known_verbs="fetch search read-file write-file edit-file list-files grep-files execute-shell spawn-agent wait-agent invoke-skill query-vault vault-write"
  bad_verb=""
  for v in $agent_verbs; do
    if ! echo " $known_verbs " | grep -q " $v "; then
      bad_verb="$v"
      break
    fi
  done
  if [ -z "$bad_verb" ]; then
    ok "$agent_id allowed_verbs all valid"
  else
    fail "$agent_id references unknown verb '$bad_verb'"
  fi
done

echo ""
echo "── Skill contract compliance ──"

for skill_dir in skills/*/; do
  skill_id=$(basename "$skill_dir")
  skill_file="${skill_dir}SKILL.md"
  if [ ! -f "$skill_file" ]; then continue; fi

  if head -30 "$skill_file" | grep -q "^name:" && \
     head -30 "$skill_file" | grep -q "^description:" && \
     head -30 "$skill_file" | grep -q "^invocable_by:"; then
    ok "$skill_id frontmatter has name/description/invocable_by"
  else
    fail "$skill_id frontmatter incomplete"
  fi
done

echo ""
echo "── Integration manifest compliance ──"

for m in integrations/*/manifest.json; do
  id=$(basename "$(dirname "$m")")
  # Must have id, name, type, requires_key, env_vars
  if python3 -c "
import json,sys
m = json.load(open('$m'))
required = ['id','name','type','requires_key','env_vars','invocable_by']
missing = [k for k in required if k not in m]
if missing:
    print('missing:', missing); sys.exit(1)
if m['id'] != '$id':
    print('id mismatch:', m['id'], 'vs', '$id'); sys.exit(1)
" 2>/dev/null; then
    ok "integrations/$id manifest complete"
  else
    fail "integrations/$id manifest incomplete or id mismatch"
  fi
done

echo ""
echo "── Feed source manifest compliance ──"

for m in monitoring/feeds/sources/*/manifest.json; do
  id=$(basename "$(dirname "$m")")
  if python3 -c "
import json,sys
m = json.load(open('$m'))
required = ['id','name','category','requires_key','env_vars']
missing = [k for k in required if k not in m]
if missing:
    print('missing:', missing); sys.exit(1)
if m['id'] != '$id':
    print('id mismatch:', m['id'], 'vs', '$id'); sys.exit(1)
" 2>/dev/null; then
    ok "monitoring/feeds/sources/$id manifest complete"
  else
    fail "monitoring/feeds/sources/$id manifest incomplete or id mismatch"
  fi
done

echo ""
echo "── Sample data validates against schemas ──"

if ! python3 -c "import jsonschema" 2>/dev/null; then
  printf "%s⊘%s jsonschema not installed — skipping sample validation%s\n" "$_c_dim" "$_c_reset" "$_c_dim"
  printf "   Install: pip3 install --user jsonschema   (CI installs this automatically)%s\n" "$_c_reset"
else
  for pair in "findings.sample.json:findings.schema.json" "fact-check.sample.json:fact-check.schema.json"; do
    sample="tests/fixtures/${pair%%:*}"
    schema="schemas/${pair##*:}"
    if python3 -c "
import json
from jsonschema import validate
validate(instance=json.load(open('$sample')), schema=json.load(open('$schema')))
" 2>/dev/null; then
      ok "$(basename "$sample") validates against $(basename "$schema")"
    else
      fail "$(basename "$sample") fails validation against $(basename "$schema")"
    fi
  done
fi

echo ""
echo "── RUNTIMES consistency (setup.html) ──"

# Extract RUNTIMES keys from setup.html, radio values, and cloud-usage-* IDs.
# All three sets must match (minus 'local' which is a separate mode, not a cloud-runtime radio).
runtimes_keys=$(grep -E "^    (local|claude|codex|gemini|opencode|[a-z]+): \{$" setup.html | sed 's/^    //' | sed 's/: {$//' | sort -u)
radio_values=$(grep -oE 'name="cloud_runtime" value="[^"]+"' setup.html | sed 's/.*value="\([^"]*\)".*/\1/' | sort -u)
usage_ids=$(grep -oE 'id="cloud-usage-[a-z]+"' setup.html | sed 's/.*cloud-usage-\([a-z]*\)".*/\1/' | sort -u)

# RUNTIMES minus 'local' should equal the radio_values set
runtimes_cloud=$(echo "$runtimes_keys" | grep -v '^local$' | sort -u)

if [ "$runtimes_cloud" = "$radio_values" ] && [ "$radio_values" = "$usage_ids" ]; then
  ok "RUNTIMES keys, radio values, and cloud-usage-* IDs all match"
else
  fail "drift between RUNTIMES / radios / usage-card IDs"
  printf "%s  RUNTIMES (cloud): %s%s\n" "$_c_dim" "$(echo $runtimes_cloud | tr '\n' ' ')" "$_c_reset"
  printf "%s  radios:           %s%s\n" "$_c_dim" "$(echo $radio_values | tr '\n' ' ')" "$_c_reset"
  printf "%s  usage IDs:        %s%s\n" "$_c_dim" "$(echo $usage_ids | tr '\n' ' ')" "$_c_reset"
fi

echo ""
if [ $FAIL -eq 0 ]; then
  printf "%s✓ All %d eval checks passed%s\n" "$_c_green" "$PASS" "$_c_reset"
  exit 0
else
  printf "%s✗ %d failed / %d passed%s\n" "$_c_red" "$FAIL" "$PASS" "$_c_reset"
  exit 1
fi
