#!/usr/bin/env bash
# Spotlight is installed through Indicator Labs. This file remains so old
# curl|bash pipes fail closed instead of fetching configurator assets or
# opening a localhost configure page.
set -euo pipefail

JOIN='https://buriedsignals.com/join'

cat <<EOF
Spotlight is installed in Indicator Labs, not by this script.

Journalists: $JOIN
Contributors: clone this repository and install Spotlight from Indicator Labs
against the local checkout. Runtime and OpenKnowledge setup live there.

There is no localhost configure.html server and no curl|bash public installer.
This script no longer accepts SPOTLIGHT_CONFIG.
EOF
exit 1
