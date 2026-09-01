#!/usr/bin/env bash
# Public Pages keep managed join CTAs while README documents the shared Engine path.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
note() { printf 'FAIL  %s\n' "$1"; fail=1; }

if [ -e setup.html ]; then
  note "setup.html must be deleted; do not leave a curl|bash stub"
fi
if [ -e install/configure.html ]; then
  note "install/configure.html must be deleted; both credential paths use Engine outside Spotlight"
fi

JOIN='https://buriedsignals.com/join'
GITHUB='https://github.com/buriedsignals/spotlight'
BOOTSTRAP='https://navigator.indicator.media/api/artifacts/bootstrap/bsig/<platform>'

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

if ! grep -qF "$BOOTSTRAP" README.md; then
  note "README.md missing public Engine bootstrap descriptor"
fi
if ! grep -qF 'bsig configure plan spotlight' README.md; then
  note "README.md missing the single Engine planning path"
fi
if ! grep -qF 'bsig keys list' README.md; then
  note "README.md missing open-source credential ID discovery"
fi
if grep -qF "$BOOTSTRAP" install-spotlight.sh; then
  note "install-spotlight.sh must remain a fail-closed pointer, not a second bootstrap path"
fi

if grep -qF 'spotlight.buriedsignals.com/setup.html' sitemap.xml; then
  note "sitemap.xml still lists setup.html"
fi
if grep -qF 'configure.html' sitemap.xml; then
  note "sitemap.xml still lists configure.html"
fi

[ "$fail" = "0" ] && echo "journalist install CTA checks passed" || exit 1
