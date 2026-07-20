#!/usr/bin/env bash
# Spotlight's public bootstrap. It only obtains a signed Engine, hosts the
# loopback configurator, and applies the sealed plan Engine returns. Product
# files, skills, secrets, and runtime configuration are Engine-owned.
set -euo pipefail

if [ -n "${SPOTLIGHT_CONFIG:-}" ]; then
  echo "The Spotlight install method changed — this script no longer accepts SPOTLIGHT_CONFIG." >&2
  echo "Run: curl -fsSL https://spotlight.buriedsignals.com/install-spotlight.sh | bash" >&2
  exit 1
fi

for arg in "$@"; do
  case "$arg" in
    --headless)
      echo "The public Spotlight bootstrap is interactive. For automation, use 'bsig configure describe spotlight' followed by 'bsig configure plan spotlight'." >&2
      exit 1
      ;;
    --dry-run)
      echo "The public Spotlight bootstrap creates a signed Engine plan. Use 'bsig configure describe spotlight' to inspect a headless plan." >&2
      exit 1
      ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

CONFIGURATOR_VERSION="1"
SPOTLIGHT_PROFILE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/spotlight"
ENGINE_PUBLIC_KEY='RWRVGhTzAGx7pqB8NEMCPW8uMr10Koa3wSoIH9OCqoCkL4GUqhQcwtU6'
NAVIGATOR_URL='https://navigator.indicator.media'
TMP_ASSETS=""

warn() { printf 'Spotlight: %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

cleanup() {
  [ -n "$TMP_ASSETS" ] && [ -d "$TMP_ASSETS" ] && rm -rf "$TMP_ASSETS"
}
trap cleanup EXIT INT TERM

find_engine() {
  if [ -n "${BSIG_BIN:-}" ] && [ -x "$BSIG_BIN" ]; then return 0; fi
  if have bsig; then BSIG_BIN="$(command -v bsig)"; return 0; fi
  for candidate in \
    '/Applications/Indicator Labs.app/Contents/Resources/bsig' \
    "$HOME/Applications/Indicator Labs.app/Contents/Resources/bsig"; do
    if [ -x "$candidate" ]; then BSIG_BIN="$candidate"; return 0; fi
  done
  return 1
}

# Navigator publishes only an allowlisted headless artifact here. Minisign
# authenticates the bytes independently of the public grant URL.
bootstrap_engine() {
  find_engine && return 0
  if ! have python3 || ! have curl || ! have minisign; then
    warn "A fresh OpenKnowledge install needs python3, curl, and minisign to verify Engine. Install minisign (macOS: brew install minisign), then re-run."
    return 1
  fi
  local os arch platform tmp release tag archive_url signature_url archive extracted root
  case "$(uname -s)" in Darwin) os=darwin ;; Linux) os=linux ;; *) warn "No signed Engine archive is published for $(uname -s). Install Indicator Labs or bsig manually."; return 1 ;; esac
  case "$(uname -m)" in arm64|aarch64) arch=arm64 ;; x86_64|amd64) arch=amd64 ;; *) warn "No signed Engine archive is published for $(uname -m). Install Indicator Labs or bsig manually."; return 1 ;; esac
  platform="$os-$arch"
  tmp="$(mktemp -d)"
  if ! curl -fsSL "$NAVIGATOR_URL/api/artifacts/bootstrap/bsig/$platform" > "$tmp/release.json"; then
    rm -rf "$tmp"; return 1
  fi
  if ! python3 - "$tmp/release.json" > "$tmp/selection" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    release = json.load(handle)
version = str(release.get("version", ""))
archive = str(release.get("archive_url", ""))
signature = str(release.get("signature_url", ""))
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", version):
    raise SystemExit("invalid version")
for value in (archive, signature):
    if not value.startswith("https://api.buriedsignals.com/v1/artifacts/download?grant="):
        raise SystemExit("invalid public artifact URL")
print("v" + version); print(archive); print(signature)
PY
  then
    rm -rf "$tmp"; warn "No complete signed public $platform Engine archive is available."; return 1
  fi
  tag="$(sed -n '1p' "$tmp/selection")"; archive_url="$(sed -n '2p' "$tmp/selection")"; signature_url="$(sed -n '3p' "$tmp/selection")"
  archive="$tmp/engine.tar.gz"
  if ! curl -fsSL "$archive_url" -o "$archive" || ! curl -fsSL "$signature_url" -o "$archive.minisig" || ! minisign -Vm "$archive" -P "$ENGINE_PUBLIC_KEY" >/dev/null; then
    rm -rf "$tmp"; warn "The signed Engine archive could not be verified; nothing was installed."; return 1
  fi
  extracted="bsig-$platform/bsig"
  if ! tar -tzf "$archive" | grep -qx "$extracted"; then
    rm -rf "$tmp"; warn "The verified Engine archive has an unexpected layout; nothing was installed."; return 1
  fi
  tar -xzf "$archive" -C "$tmp"
  root="$HOME/.local/share/buriedsignals/engine/${tag#v}"
  mkdir -p "$root" "$HOME/.local/bin"
  install -m 755 "$tmp/$extracted" "$root/bsig"
  ln -sfn "$root/bsig" "$HOME/.local/bin/bsig"
  rm -rf "$tmp"
  BSIG_BIN="$root/bsig"; export BSIG_BIN
  printf 'Spotlight: Buried Signals Engine %s verified and installed.\n' "$tag"
}

bootstrap_engine || exit 1
export BSIG_BIN

if ! have python3 || ! python3 -c 'pass' >/dev/null 2>&1; then
  warn "python3 is required for Spotlight's local configurator. Install it, then re-run."
  exit 1
fi

TMP_ASSETS="$(mktemp -d)"
mkdir -p "$TMP_ASSETS/install"
for asset in setup_server.py configure.html engine_bridge.py; do
  curl -fsSL "https://spotlight.buriedsignals.com/install/$asset" -o "$TMP_ASSETS/install/$asset"
done
if ! grep -q "CONFIGURATOR_VERSION = \"$CONFIGURATOR_VERSION\"" "$TMP_ASSETS/install/setup_server.py" \
  || ! grep -q "configurator-version\" content=\"$CONFIGURATOR_VERSION\"" "$TMP_ASSETS/install/configure.html"; then
  warn "Configurator version mismatch; the public site may still be propagating. Retry shortly."
  exit 1
fi

mkdir -p "$SPOTLIGHT_PROFILE_DIR"
ENGINE_PLAN_MARKER="$SPOTLIGHT_PROFILE_DIR/engine-plan.ready"
rm -f "$ENGINE_PLAN_MARKER"
printf 'Spotlight: opening the local configurator. Choices and credentials stay on 127.0.0.1.\n'
python3 "$TMP_ASSETS/install/setup_server.py" --profile-dir "$SPOTLIGHT_PROFILE_DIR" --repo-dir "$TMP_ASSETS"

if [ ! -f "$ENGINE_PLAN_MARKER" ]; then
  warn "Engine did not return a sealed Spotlight plan; no legacy Obsidian/QMD fallback was applied."
  exit 1
fi
ENGINE_PLAN_PATH="$(python3 - "$ENGINE_PLAN_MARKER" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle: marker = json.load(handle)
path = marker.get("plan", {}).get("plan_path", "")
binary = marker.get("engine_binary", "")
if not isinstance(path, str) or not path or not isinstance(binary, str) or not binary:
    raise SystemExit("Engine plan marker is incomplete")
print(path)
PY
)"
ENGINE_BINARY="$(python3 - "$ENGINE_PLAN_MARKER" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle: marker = json.load(handle)
binary = marker.get("engine_binary", "")
if not isinstance(binary, str) or not binary: raise SystemExit("Engine plan marker is incomplete")
print(binary)
PY
)"
[ -x "$ENGINE_BINARY" ] || { warn "The Engine binary recorded by setup is unavailable: $ENGINE_BINARY"; exit 1; }
"$ENGINE_BINARY" apply "$ENGINE_PLAN_PATH"
"$ENGINE_BINARY" welcome spotlight
