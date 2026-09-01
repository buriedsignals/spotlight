#!/usr/bin/env bash
# Spotlight is installed through Engine. Indicator Labs provides the managed
# path; this file remains so old curl|bash pipes fail closed instead of fetching
# configurator assets or opening a localhost configure page.
set -euo pipefail

JOIN='https://buriedsignals.com/join'

cat <<EOF
This script is a fail-closed pointer; it does not install Spotlight.

Managed journalists: install with Indicator Labs at $JOIN
Open-source and agent-led users: follow README.md's signed Engine instructions.
Configure any required credential IDs through Engine's protected bsig
stdin/keychain flow. Let a trusted local agent prepare the command, then enter
each value only in a private prompt—never chat, argv, shell history, or a
repository file.

There is no localhost configure.html server and no curl|bash public installer.
This script no longer accepts SPOTLIGHT_CONFIG.
EOF
exit 1
