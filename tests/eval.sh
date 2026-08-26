#!/usr/bin/env bash
# Spotlight eval — contract compliance + sample data validation.
#
# Goes beyond smoke.sh's structural checks: validates that agent + skill
# frontmatter is well-formed, allowed_verbs reference real verbs from
# AGENTS.md, sample case files validate against their schemas, and the
# runtime IDs in install/navigator_bridge.py are consistent.
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
echo "── Sample data validates against schemas ──"

if ! python3 -c "import jsonschema" 2>/dev/null; then
  printf "%s⊘%s jsonschema not installed — skipping sample validation%s\n" "$_c_dim" "$_c_reset" "$_c_dim"
  printf "   Install: python3 -m pip install --user jsonschema==4.25.1   (CI installs this automatically)%s\n" "$_c_reset"
else
  for pair in "findings.sample.json:findings.schema.json" "fact-check.sample.json:fact-check.schema.json" "evidence-bundle.sample.json:evidence-bundle.schema.json" "provenance-manifest.sample.json:provenance-manifest.schema.json" "knowledge-batch.sample.json:knowledge-batch.schema.json"; do
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
  if python3 -c "
import json
from copy import deepcopy
from jsonschema import ValidationError, validate
s = json.load(open('schemas/fact-check.schema.json'))
base = json.load(open('tests/fixtures/fact-check.sample.json'))
invalid = []
d = deepcopy(base); d['fact_checks'] = []; invalid.append(d)
d = deepcopy(base); d['claims'][0]['claim'] = d['claims'][0]['claim_text']; invalid.append(d)
d = deepcopy(base); d['claims'][0]['evidence_for'][0]['local_file'] = None; invalid.append(d)
d = deepcopy(base); d['claims'][0]['evidence_for'][0].pop('access_method'); invalid.append(d)
for value in invalid:
    try: validate(instance=value, schema=s)
    except ValidationError: continue
    raise SystemExit(1)
" 2>/dev/null; then
    ok "fact-check schema rejects container, alias, and null-anchor ambiguity"
  else
    fail "fact-check schema accepts a runtime-invalid ambiguity"
  fi
fi

echo ""
echo "── Knowledge projection acceptance ──"

for t in knowledge-destination-check knowledge-destination-hardening-check knowledge-projection-check query-vault-check graph-lookup-migration-check ingest-check schema-validation-check; do
  if python3 "tests/$t.py" >/dev/null 2>&1; then
    ok "tests/$t.py passes"
  else
    fail "tests/$t.py failed"
  fi
done

echo ""
echo "── Runtime consistency (install/navigator_bridge.py) ──"

if python3 - <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path("install").resolve()))
from navigator_bridge import NAVIGATOR_RUNTIME_IDS
expected = {"claude-code", "codex-cli", "opencode", "pi"}
have = {choice for choice, runtime in NAVIGATOR_RUNTIME_IDS.items() if choice == runtime}
if have != expected:
    raise SystemExit(f"runtime id drift: {sorted(have)} != {sorted(expected)}")
print("navigator runtime ids cover claude-code/codex-cli/opencode/pi")
PY
then
  ok "navigator runtime ids cover claude-code/codex-cli/opencode/pi"
else
  fail "navigator runtime id drift in install/navigator_bridge.py"
fi

echo ""
echo "── Shell safety regression ──"

if python3 tests/shell-safety-check.py >/dev/null 2>&1; then
  ok "hostile shell inputs are rejected"
else
  fail "shell-safety regression failed"
fi

echo ""
echo "── Skills manifest regression ──"

if python3 tests/skills-manifest-check.py >/dev/null 2>&1; then
  ok "skills-manifest.json matches skill dirs and AGENTS.md registry"
else
  fail "skills-manifest regression failed"
fi

echo ""
echo "── SkillOpt-lite harness regression ──"

if bash evals/skillopt-lite.sh --self-test >/dev/null 2>&1; then
  ok "SkillOpt-lite harness self-test passes"
else
  fail "SkillOpt-lite harness self-test failed"
fi

echo ""
echo "── Methodology Navigator regression ──"

if python3 tests/methodology-navigator-check.py >/dev/null 2>&1; then
  ok "Navigator-green methodology requires saved Navigator evidence"
else
  fail "methodology Navigator regression failed"
fi

echo ""
echo "── Provenance manifest regression ──"

if python3 tests/provenance-manifest-check.py >/dev/null 2>&1; then
  ok "provenance manifest builder emits unsigned Noosphere C2PA contract"
else
  fail "provenance manifest regression failed"
fi

echo ""
echo "── Review artifact regression ──"

if node tests/review-template-check.js >/dev/null 2>&1; then
  ok "review artifact renders grounding and C2PA provenance state"
else
  fail "review artifact regression failed"
fi

echo ""
echo "── Report template safety regression ──"

if python3 tests/report-template-safety-check.py >/dev/null 2>&1; then
  ok "report template contains no hidden HTML comments"
else
  fail "report template safety regression failed"
fi

echo ""
echo "── validate-case.py smoke ──"

# Wrap the existing sample fixtures into a fake case dir and run the validator.
_VC_TMP=$(mktemp -d -t spotlight-validate-case.XXXXXX)
mkdir -p "$_VC_TMP/data"
cp tests/fixtures/findings.sample.json "$_VC_TMP/data/findings.json"
cp tests/fixtures/fact-check.sample.json "$_VC_TMP/data/fact-check.json"
cp tests/fixtures/evidence-bundle.sample.json "$_VC_TMP/data/evidence-bundle.json"
python3 -c "
import json
json.dump({
  'schema_version': '1.0',
  'project': 'sample-investigation',
  'cycles': [{
    'cycle': 1,
    'timestamp': '2026-04-17T15:00:00Z',
    'focus': 'sample validation',
    'methodology': {
      'techniques_used': ['registry lookup'],
      'tools_used': ['firecrawl'],
      'search_queries': ['Example Corp Ltd BVI'],
      'failed_approaches': []
    },
    'sources_consulted': [{
      'url': 'https://bvifsc.example/filings/2019-ABC-123',
      'type': 'registry',
      'accessed': '2026-04-17T12:03:00Z',
      'useful': True
    }],
    'findings_added': 1,
    'findings_upgraded': 0,
    'gaps_resolved': [],
    'gaps_remaining': ['Primary UBO disclosure'],
    'notes': 'Sample log.'
  }]
}, open('$_VC_TMP/data/investigation-log.json', 'w'))
"
if python3 scripts/validate-case.py "$_VC_TMP" >/dev/null 2>&1; then
  ok "validate-case.py passes the sample fixtures"
else
  fail "validate-case.py rejected its own sample fixtures"
fi
rm -rf "$_VC_TMP"

# Negative test: a case with an empty 'claim' must fail
_VC_NEG=$(mktemp -d -t spotlight-validate-case-neg.XXXXXX)
mkdir -p "$_VC_NEG/data"
python3 -c "
import json, sys
d = json.load(open('tests/fixtures/findings.sample.json'))
d['findings'][0]['claim'] = ''
json.dump(d, open('$_VC_NEG/data/findings.json', 'w'))
"
if python3 scripts/validate-case.py "$_VC_NEG" >/dev/null 2>&1; then
  fail "validate-case.py FAILED to reject empty 'claim' (negative test)"
else
  ok "validate-case.py rejects empty 'claim' (negative test)"
fi
rm -rf "$_VC_NEG"

# Negative test: detailed fact-check evidence items must satisfy the published schema.
_VC_NEG=$(mktemp -d -t spotlight-validate-case-fact-neg.XXXXXX)
mkdir -p "$_VC_NEG/data"
cp tests/fixtures/findings.sample.json "$_VC_NEG/data/findings.json"
python3 -c "
import json
d = json.load(open('tests/fixtures/fact-check.sample.json'))
d['claims'][0]['evidence_for'][0].pop('source')
json.dump(d, open('$_VC_NEG/data/fact-check.json', 'w'))
"
if python3 scripts/validate-case.py "$_VC_NEG" >/dev/null 2>&1; then
  fail "validate-case.py FAILED to reject malformed fact-check evidence item"
else
  ok "validate-case.py rejects malformed fact-check evidence item"
fi
rm -rf "$_VC_NEG"

# Negative test: evidence-bundle.json must satisfy its published schema when present.
_VC_NEG=$(mktemp -d -t spotlight-validate-case-evidence-neg.XXXXXX)
mkdir -p "$_VC_NEG/data"
cp tests/fixtures/findings.sample.json "$_VC_NEG/data/findings.json"
cp tests/fixtures/fact-check.sample.json "$_VC_NEG/data/fact-check.json"
python3 -c "
import json
d = json.load(open('tests/fixtures/evidence-bundle.sample.json'))
d['items'][0].pop('source_url')
json.dump(d, open('$_VC_NEG/data/evidence-bundle.json', 'w'))
"
if python3 scripts/validate-case.py "$_VC_NEG" >/dev/null 2>&1; then
  fail "validate-case.py FAILED to reject malformed evidence-bundle.json"
else
  ok "validate-case.py rejects malformed evidence-bundle.json"
fi
rm -rf "$_VC_NEG"

# Negative test: investigation-log.json must satisfy its published schema when present.
_VC_NEG=$(mktemp -d -t spotlight-validate-case-log-neg.XXXXXX)
mkdir -p "$_VC_NEG/data"
cp tests/fixtures/findings.sample.json "$_VC_NEG/data/findings.json"
cp tests/fixtures/fact-check.sample.json "$_VC_NEG/data/fact-check.json"
python3 -c "
import json
json.dump({
  'schema_version': '1.0',
  'project': 'sample-investigation',
  'cycles': [{
    'cycle': 1,
    'timestamp': '2026-04-17T15:00:00Z',
    'focus': 'sample validation',
    'methodology': {'techniques_used': ['registry lookup']},
    'findings_added': 1,
    'gaps_remaining': []
  }]
}, open('$_VC_NEG/data/investigation-log.json', 'w'))
"
if python3 scripts/validate-case.py "$_VC_NEG" >/dev/null 2>&1; then
  fail "validate-case.py FAILED to reject malformed investigation-log.json"
else
  ok "validate-case.py rejects malformed investigation-log.json"
fi
rm -rf "$_VC_NEG"

echo ""
if [ $FAIL -eq 0 ]; then
  printf "%s✓ All %d eval checks passed%s\n" "$_c_green" "$PASS" "$_c_reset"
  exit 0
else
  printf "%s✗ %d failed / %d passed%s\n" "$_c_red" "$FAIL" "$PASS" "$_c_reset"
  exit 1
fi
