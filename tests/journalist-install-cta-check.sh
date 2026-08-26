#!/usr/bin/env bash
# Public Pages must send journalists to join/desktop, not curl|bash or configure.html.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
note() { printf 'FAIL  %s\n' "$1"; fail=1; }

if [ -e setup.html ]; then
  note "setup.html must be deleted; do not leave a curl|bash stub"
fi
if [ -e install/configure.html ]; then
  note "install/configure.html must be deleted; key collection lives in Indicator Labs"
fi

JOIN='https://buriedsignals.com/join'
GITHUB='https://github.com/buriedsignals/spotlight'

for page in index.html docs/index.html going-sovereign/index.html; do
  if ! grep -qF "$JOIN" "$page"; then
    note "$page missing Install href $JOIN"
  fi
  if ! grep -qF "$GITHUB" "$page"; then
    note "$page missing GitHub link"
  fi
  if grep -qE 'href=["'"'"'][^"'"'"']*(setup|configure)\.html' "$page"; then
    note "$page still links to setup.html or configure.html"
  fi
  if grep -qE 'curl .*install-spotlight\.sh' "$page"; then
    note "$page still advertises curl|bash install-spotlight.sh"
  fi
done

if grep -qF 'spotlight.buriedsignals.com/setup.html' sitemap.xml; then
  note "sitemap.xml still lists setup.html"
fi
if grep -qF 'configure.html' sitemap.xml; then
  note "sitemap.xml still lists configure.html"
fi

[ "$fail" = "0" ] && echo "journalist install CTA checks passed" || exit 1
