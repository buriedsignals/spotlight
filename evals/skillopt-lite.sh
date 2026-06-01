#!/usr/bin/env bash
# Bounded SkillOpt-lite scoring harness.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
  cat <<'USAGE'
usage:
  bash evals/skillopt-lite.sh --self-test
  bash evals/skillopt-lite.sh <skill> <split> <actual-output-dir>

Runs deterministic graders over evals/fixtures/<split>/<skill>/*.expected.json
and matching files in <actual-output-dir>/*.actual.json.
USAGE
}

if [ "${1:-}" = "--self-test" ]; then
  tmp="$(mktemp -d -t spotlight-skillopt.XXXXXX)"
  expected="$tmp/expected.json"
  actual="$tmp/actual.json"
  cat > "$expected" <<'JSON'
{"integration":"osint-navigator"}
JSON
  cat > "$actual" <<'JSON'
{"integration":"osint-navigator","fallback":"curated catalog","preflight_status":"green","sensitive_mode_safe":true,"minimal_payload":true}
JSON
  python3 "$ROOT/evals/graders/integrations.py" "$expected" "$actual" >/dev/null
  rc=$?
  rm -rf "$tmp"
  if [ "$rc" -eq 0 ]; then
    echo "skillopt-lite self-test: OK"
  fi
  exit "$rc"
fi

if [ "$#" -ne 3 ]; then
  usage >&2
  exit 2
fi

skill="$1"
split="$2"
actual_dir="$3"
fixture_dir="$ROOT/evals/fixtures/$split/$skill"
grader="$ROOT/evals/graders/$skill.py"

if [ ! -f "$grader" ]; then
  echo "grader not found: $grader" >&2
  exit 2
fi
if [ ! -d "$fixture_dir" ]; then
  echo "fixture split not found: $fixture_dir" >&2
  exit 2
fi
if [ ! -d "$actual_dir" ]; then
  echo "actual output dir not found: $actual_dir" >&2
  exit 2
fi

pass=0
fail=0
for expected in "$fixture_dir"/*.expected.json; do
  [ -e "$expected" ] || continue
  name="$(basename "$expected" .expected.json)"
  actual="$actual_dir/$name.actual.json"
  if [ ! -f "$actual" ]; then
    echo "missing actual output: $actual" >&2
    fail=$((fail + 1))
    continue
  fi
  if python3 "$grader" "$expected" "$actual" >/dev/null; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
  fi
done

if [ "$fail" -eq 0 ]; then
  echo "skillopt-lite: $pass passed / $fail failed"
  exit 0
fi

echo "skillopt-lite: $pass passed / $fail failed" >&2
exit 1
