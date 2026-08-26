#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

fixture="$(mktemp -d)"
trap 'rm -rf "$fixture"' EXIT
export HOME="$fixture/home"
export SHELL=/bin/zsh
product_root="$HOME/spotlight"
channel="$HOME/.local/share/buriedsignals/public-installer/spotlight"
bin="$HOME/.local/bin"
mkdir -p "$product_root/scripts" "$channel" "$bin" "$HOME/.config/spotlight"
cp scripts/spotlight-uninstall "$product_root/scripts/spotlight-uninstall"
printf 'SPOTLIGHT_DIR_INPUT=%q\nSPOTLIGHT_RUNTIME=codex\n' "$product_root" > "$HOME/.config/spotlight/setup-config.env"
ln -s "$product_root/scripts/navigator-connect" "$bin/spotlight-navigator"
ln -s "$product_root/scripts/spotlight-uninstall" "$bin/spotlight-uninstall"
printf '%s\n' '#!/usr/bin/env bash' 'SPOTLIGHT_DIR_DEFAULT_INPUT=x' 'echo "Spotlight doctor: OK"' > "$bin/spotlight-doctor"
printf '%s\n' '#!/usr/bin/env bash' 'SPOTLIGHT_DIR_DEFAULT_INPUT=x' 'echo "signed public update channel is missing"' > "$bin/spotlight-update"
printf '%s\n' '#!/usr/bin/env bash' '# user-owned file' > "$bin/spotlight-local"
printf '%s\n' '# user setting' '# SPOTLIGHT-BEGIN — added by install-spotlight.sh' 'spotlight() { :; }' '# SPOTLIGHT-END' '# trailing setting' > "$HOME/.zshrc"

bash "$product_root/scripts/spotlight-uninstall" --dry-run >/dev/null
[ -L "$bin/spotlight-uninstall" ]
grep -q '^# SPOTLIGHT-BEGIN' "$HOME/.zshrc"
[ -d "$channel" ]

out="$(bash "$product_root/scripts/spotlight-uninstall" 2>&1)"
printf '%s\n' "$out" | grep -qF 'https://buriedsignals.com/join' || {
  echo "spotlight-uninstall must point leftover users at Indicator Labs"
  exit 1
}
[ ! -e "$bin/spotlight-navigator" ]
[ ! -e "$bin/spotlight-uninstall" ]
[ ! -e "$bin/spotlight-doctor" ]
[ ! -e "$bin/spotlight-update" ]
[ -f "$bin/spotlight-local" ]
[ ! -e "$channel" ]
[ -d "$product_root" ]
grep -q '^# user setting$' "$HOME/.zshrc"
grep -q '^# trailing setting$' "$HOME/.zshrc"
! grep -q '^# SPOTLIGHT-' "$HOME/.zshrc"
printf 'spotlight uninstall cleanup checks passed\n'
