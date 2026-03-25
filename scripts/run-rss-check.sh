#!/usr/bin/env bash
# Wrapper to restrict python3 execution to parse-rss.py only.
# Usage: run-rss-check.sh [--json] [--since <duration>]
set -euo pipefail
cd -- "$HOME/buried_signals/newsroom/spotlight"
exec python3 scripts/parse-rss.py "$@"
