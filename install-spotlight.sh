#!/usr/bin/env bash
# Spotlight installer — one static, reviewable, key-free script:
#
#   curl -fsSL https://spotlight.buriedsignals.com/install-spotlight.sh | bash
#
# Interactive flow: the script launches a local configurator
# (install/setup_server.py) on 127.0.0.1; every choice and API key is entered
# on that local page and staged in ~/.config/spotlight/ (setup-config.env +
# .env, both 0600). The script sources the staged artifacts and the install
# body takes over. Keys never appear in the shell command line, in shell
# history, or on any hosted page.
#
# Headless / CI path:
#   curl -fsSL https://spotlight.buriedsignals.com/install-spotlight.sh | bash -s -- --headless
# with the required env vars pre-exported. Load keys from a 0600 env file
# (set -a; . keys.env; set +a) — never inline `export KEY=...` commands — so
# the no-keys-in-shell-history guarantee holds on this path too.

set -euo pipefail

# The retired SPOTLIGHT_CONFIG base64-blob channel fails loud — never decoded.
if [ -n "${SPOTLIGHT_CONFIG:-}" ]; then
  echo "The Spotlight install method changed — this script no longer accepts SPOTLIGHT_CONFIG." >&2
  echo "Run the new installer instead:" >&2
  echo "  curl -fsSL https://spotlight.buriedsignals.com/install-spotlight.sh | bash" >&2
  exit 1
fi

# Arg parse: --dry-run prints what the body would do without touching the
# system; --headless skips the configurator and reads pre-exported env vars
# (the :? guards below enforce the required set). Plain --dry-run without
# --headless still runs the live configurator — artifacts are staged, only
# the install body is dry-run.
DRY_RUN=0
HEADLESS=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --headless) HEADLESS=1 ;;
    *) ;;
  esac
done

# Version handshake with install/setup_server.py + install/configure.html —
# asserted after a CDN fetch so a half-propagated deploy fails loud instead
# of 403-ing every POST.
CONFIGURATOR_VERSION="1"
SPOTLIGHT_PROFILE_DIR="$HOME/.config/spotlight"
STAGED_ENV="$SPOTLIGHT_PROFILE_DIR/.env"
SETUP_CONFIG="$SPOTLIGHT_PROFILE_DIR/setup-config.env"
SPOTLIGHT_INSTALL_DONE=0
CONFIGURATOR_RAN=0

if [ "$HEADLESS" = "1" ]; then
  echo "→ Headless install: reading configuration from pre-exported environment variables."
else
  # ── Reuse gate: a completed install reconfigures without retyping keys ──
  # Resolve the candidate install dir from the environment (the shell-rc
  # block exports SPOTLIGHT_DIR), falling back to the SPOTLIGHT_DIR_INPUT
  # retained in setup-config.env from the previous configurator run.
  REUSE_CANDIDATE="${SPOTLIGHT_DIR:-}"
  if [ -z "$REUSE_CANDIDATE" ] && [ -f "$SETUP_CONFIG" ]; then
    REUSE_CANDIDATE="$( . "$SETUP_CONFIG" >/dev/null 2>&1 || true; printf '%s' "${SPOTLIGHT_DIR_INPUT:-}" )"
    case "$REUSE_CANDIDATE" in
      "~") REUSE_CANDIDATE="$HOME" ;;
      "~/"*) REUSE_CANDIDATE="$HOME/${REUSE_CANDIDATE:2}" ;;
    esac
  fi
  REUSED=0
  if [ -n "$REUSE_CANDIDATE" ] && [ -f "$REUSE_CANDIDATE/.spotlight-config.json" ] && [ -f "$REUSE_CANDIDATE/.env" ]; then
    echo "Found an existing Spotlight install at $REUSE_CANDIDATE. Reuse its configuration? [Y/n]"
    read -r ans </dev/tty || ans="Y"
    if [[ ! "$ans" =~ ^[Nn] ]]; then
      set -a
      . "$REUSE_CANDIDATE/.env"
      if [ -f "$SETUP_CONFIG" ]; then . "$SETUP_CONFIG"; fi
      set +a
      REUSED=1
      echo "→ Reusing configuration from $REUSE_CANDIDATE/.env"
    fi
  fi

  if [ "$REUSED" != "1" ]; then
    # python3 must actually execute, not merely resolve on PATH — a fresh mac
    # ships a /usr/bin/python3 shim that dies until the Command Line Tools
    # are installed, and the configurator needs a working interpreter.
    if ! python3 -c 'pass' >/dev/null 2>&1; then
      if [ "$(uname -s)" = "Darwin" ]; then
        echo "python3 cannot run yet — macOS needs the Xcode Command Line Tools for the configurator."
        echo "Install the Command Line Tools now? [Y/n]"
        read -r ans </dev/tty || ans="Y"
        if [[ "$ans" =~ ^[Nn] ]]; then echo "Aborted." >&2; exit 1; fi
        xcode-select --install || true
        echo "A dialog opened. Complete the Command Line Tools install, then re-run this script:"
        echo "  curl -fsSL https://spotlight.buriedsignals.com/install-spotlight.sh | bash"
        exit 1
      else
        echo "python3 is required for the configurator. Install it first (apt install python3 / dnf install python3), then re-run." >&2
        exit 1
      fi
    fi

    # SSH / display-less sessions: the configurator opens a local browser and
    # degrades gracefully to printing its URL — give port-forward + headless
    # guidance instead of failing silently, then continue.
    if [ -n "${SSH_TTY:-}" ] || { [ "$(uname -s)" = "Linux" ] && [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; }; then
      echo ""
      echo "  This looks like an SSH or display-less session. The configurator serves a"
      echo "  page on 127.0.0.1 and prints its URL below. Over SSH, forward that port"
      echo "  from your local machine (ssh -L PORT:127.0.0.1:PORT <host>) and open the"
      echo "  printed URL in your local browser — or run the headless install with"
      echo "  pre-exported env vars loaded from a 0600 env file:"
      echo "    curl -fsSL https://spotlight.buriedsignals.com/install-spotlight.sh | bash -s -- --headless"
      echo ""
    fi

    # ── Configurator assets: working tree first, else fetch from Pages ──
    if [ -f "${BASH_SOURCE[0]-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/install/setup_server.py" ]; then
      CONFIGURATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    else
      TMP_ASSETS="$(mktemp -d)"
      mkdir -p "$TMP_ASSETS/install"
      curl -fsSL https://spotlight.buriedsignals.com/install/setup_server.py -o "$TMP_ASSETS/install/setup_server.py"
      curl -fsSL https://spotlight.buriedsignals.com/install/configure.html -o "$TMP_ASSETS/install/configure.html"
      if ! grep -q "CONFIGURATOR_VERSION = \"$CONFIGURATOR_VERSION\"" "$TMP_ASSETS/install/setup_server.py" \
        || ! grep -q "configurator-version\" content=\"$CONFIGURATOR_VERSION\"" "$TMP_ASSETS/install/configure.html"; then
        echo "" >&2
        echo "✗ Configurator version mismatch: this script expects configurator v$CONFIGURATOR_VERSION," >&2
        echo "  but the fetched assets disagree. The Pages CDN may still be propagating a" >&2
        echo "  deploy — retry in ~10 minutes." >&2
        exit 1
      fi
      CONFIGURATOR_DIR="$TMP_ASSETS"
    fi

    # ── Abort trap: an interrupted install never orphans staged secrets.
    # setup-config.env (no secrets) is retained for the reuse gate.
    cleanup_staged_env() {
      if [ "$SPOTLIGHT_INSTALL_DONE" != "1" ]; then rm -f "$STAGED_ENV"; fi
    }
    trap cleanup_staged_env EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    echo "→ Opening the Spotlight configurator in your browser."
    echo "  Your choices and API keys go to a local server on 127.0.0.1 only and are"
    echo "  staged in $SPOTLIGHT_PROFILE_DIR — nothing is uploaded anywhere."
    python3 "$CONFIGURATOR_DIR/install/setup_server.py" --profile-dir "$SPOTLIGHT_PROFILE_DIR" --repo-dir "$CONFIGURATOR_DIR"
    if [ ! -f "$SETUP_CONFIG" ] || [ ! -f "$STAGED_ENV" ]; then
      echo "Configuration was not completed; re-run the installer to try again." >&2
      exit 1
    fi
    set -a
    . "$SETUP_CONFIG"
    . "$STAGED_ENV"
    set +a
    CONFIGURATOR_RAN=1
  fi
fi

run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: %s\n' "$*"
  else
    "$@"
  fi
}

# === expand_path: handle ~ and ~/ in the form-supplied paths ===
expand_path() {
  local input="$1"
  if [ "$input" = "~" ]; then printf "%s\n" "$HOME"
  elif [[ "$input" == "~/"* ]]; then printf "%s/%s\n" "$HOME" "${input#~/}"
  else printf "%s\n" "$input"; fi
}

SPOTLIGHT_DIR_INPUT="${SPOTLIGHT_DIR_INPUT:?install path missing from config}"
if [ -n "${SPOTLIGHT_DIR:-}" ]; then
  SPOTLIGHT_DIR="$SPOTLIGHT_DIR"
else
  SPOTLIGHT_DIR="$(expand_path "$SPOTLIGHT_DIR_INPUT")"
fi
SPOTLIGHT_VAULT_INPUT="${SPOTLIGHT_VAULT_INPUT:?vault path missing from config}"
SPOTLIGHT_VAULT_PATH="$(expand_path "$SPOTLIGHT_VAULT_INPUT")"
if [ -n "${SPOTLIGHT_CASES_ROOT:-}" ]; then
  SPOTLIGHT_CASES_ROOT="$(expand_path "$SPOTLIGHT_CASES_ROOT")"
else
  SPOTLIGHT_CASES_ROOT="$SPOTLIGHT_DIR/cases"
fi
REPO_URL="https://github.com/buriedsignals/spotlight.git"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"

# Reviewed dependency pins. Keep this list in sync with
# VALIDATED_DEPENDENCIES.md. The installer never asks npm or pip for "latest"
# on packages managed by Spotlight setup.
FIRECRAWL_CLI_VERSION="1.3.1"
QMD_VERSION="2.5.3"
DEV_BROWSER_VERSION="0.2.8"
CLAUDE_CODE_VERSION="2.1.169"
GEMINI_CLI_VERSION="0.45.2"
OPENAI_CODEX_VERSION="0.138.0"
OPENCODE_AI_VERSION="1.17.7"
PI_CODING_AGENT_VERSION="0.79.6"
JSONSCHEMA_VERSION="4.25.1"
REQUESTS_VERSION="2.32.5"
MAIGRET_VERSION="0.4.4"

# Defaults for optional config fields
: "${SPOTLIGHT_MODE:=cloud}"
: "${SPOTLIGHT_RUNTIME:=claude}"
: "${SPOTLIGHT_LOCAL_SERVER:=}"
: "${SPOTLIGHT_LOCAL_MODEL:=gemma}"
: "${SPOTLIGHT_AGENT:=opencode}"
: "${SPOTLIGHT_OPENCODE_INTERFACE:=cli}"
: "${SPOTLIGHT_OPENCODE_PROVIDER:=}"
: "${SPOTLIGHT_CLOUD_KEY_VAR:=}"
: "${SPOTLIGHT_CLOUD_KEY:=}"
: "${SPOTLIGHT_MODEL_REPO:=}"
: "${SPOTLIGHT_VAULT_APP:=obsidian}"
: "${SPOTLIGHT_INT_DEVBROWSER:=true}"
: "${SPOTLIGHT_INT_JUNKIPEDIA:=false}"
: "${JUNKIPEDIA_API_KEY:=}"
: "${SPOTLIGHT_INT_UNPAYWALL:=false}"
: "${UNPAYWALL_EMAIL:=}"
: "${SPOTLIGHT_INT_RLM:=false}"
: "${SPOTLIGHT_RLM_MODE:=off}"
: "${SPOTLIGHT_RLM_MODEL:=}"
: "${SPOTLIGHT_RLM_PREFILTER:=}"
: "${SPOTLIGHT_RLM_HYBRID:=}"
: "${FIRECRAWL_API_KEY:?firecrawl key missing from config}"
: "${OSINT_NAV_API_KEY:?osint-navigator key missing from config}"

if [ "$SPOTLIGHT_INT_RLM" != "true" ]; then
  SPOTLIGHT_RLM_MODE="off"
  SPOTLIGHT_RLM_MODEL=""
  SPOTLIGHT_RLM_PREFILTER=""
  SPOTLIGHT_RLM_HYBRID=""
fi

# Derive model artifact names from the model selection.
#
# Two tiers, both abliterated journalist tunes, both Ollama-native + HF GGUF
# available for the llamacpp path:
#
# RECOMMENDED tier:
#   gemma31b — Google Gemma 4 31B (Q4). 32 GB Macs. RECOMMENDED default.
#             Top of the locally-runnable pack on the OSINT benchmark
#             (facet 0.881 vs the 27B tune's 0.825), lowest hallucination
#             of all 18 models (1.2%), and correctly REFUSES the doorstep/
#             children ethics probe. Non-abliterated, no thinking mode, no
#             stop-token workaround. Ollama path uses gemma4:31b-it-qat; the
#             llama-server path downloads the unsloth Q4_K_M GGUF (which the
#             current Ollama cannot import). Slower prompt-processing than the
#             27B at long context — expect patience on deep investigations.
#
#   qwen27b — Tom's Qwen 3.6 27B journalist tune. 32 GB Macs. Alternative.
#             Fine-tuned for the investigative format, refuses the ethics
#             probe, Ollama-compatible, faster than Gemma 31B. Runs in
#             thinking mode (see below).
#             Same investigative-journalism corpus as the 9B, fine-tuned on
#             Huihui's abliterated Qwen 3.6 base. Standard Q4_K_M quant.
#             Setup form's fit-check enforces 32 GB minimum before this
#             tier is selectable. Runs in thinking mode (see below).
#
# Removed in this revision:
#   - qwen9b (Tom's qwen3.5-9B journalist tune) — REMOVED for ethics failure:
#     on the doorstep/children probe it COMPLIES (compiles the targeting info)
#     rather than refusing. No small local model can safely replace it (a
#     vanilla 12B collapses on Spotlight's investigator prompt), so the small
#     local tier is dropped — <32 GB devices use Frontier/cloud mode. The HF
#     repo has been deleted.
#   - gemma-e4b (Tom's gemma4 E4B journalist) — REMOVED for the same ethics
#     failure (worse: emits family-surveillance tradecraft). HF repo deleted.
#   - gemma (unsloth gemma-4-26B-A4B MoE) — 17 GB OOMs on 16 GB Macs; the
#     active-param-vs-file-footprint trap that misled Luc.
#   - qwen27b @ HauhauCS IQ2_M — failed to load on our test machine
#     (non-standard K_P quants + mmproj vision file appear Ollama-incompat).
#   - qwen27b @ raw Huihui abliterated — superseded by Tom's journalist
#     fine-tune built on the same base. Same on-disk footprint, better
#     bench scores on investigative-journalism prompts.
case "$SPOTLIGHT_LOCAL_MODEL" in
  gemma31b)
    GGUF_FILE="gemma-4-31B-it-Q4_K_M.gguf"
    # Ollama path uses the OFFICIAL Gemma-4 31B QAT tag (loads cleanly); the
    # unsloth GGUF used on the llama-server path does NOT import into current
    # Ollama, so the two paths intentionally use different sources of the same
    # Q4 Gemma-4 31B. Gemma has no thinking mode and no stop-token bug, so it
    # needs none of the qwen27b workarounds below.
    OLLAMA_MODEL_DEFAULT="gemma4:31b-it-qat"
    OLLAMA_ALIAS_DEFAULT="spotlight-gemma31b"
    OLLAMA_SIZE_LABEL="~18 GB"
    ;;
  qwen27b)
    GGUF_FILE="qwen3.6-27b-abliterated-journalist-Q4_K_M.gguf"
    OLLAMA_MODEL_DEFAULT="hf.co/tomvaillant/qwen3.6-27b-abliterated-journalist-GGUF:Q4_K_M"
    OLLAMA_ALIAS_DEFAULT="spotlight-qwen27b"
    OLLAMA_SIZE_LABEL="~15 GB"
    ;;
  *)
    GGUF_FILE=""
    OLLAMA_MODEL_DEFAULT=""
    OLLAMA_ALIAS_DEFAULT=""
    OLLAMA_SIZE_LABEL=""
    ;;
esac

if [ "$SPOTLIGHT_LOCAL_SERVER" = "llamacpp" ]; then
  LOCAL_PORT="8080"
elif [ "$SPOTLIGHT_LOCAL_SERVER" = "ollama" ]; then
  LOCAL_PORT="11434"
else
  LOCAL_PORT=""
fi
LOCAL_BASE_URL=""
if [ -n "$LOCAL_PORT" ]; then
  LOCAL_BASE_URL="http://127.0.0.1:$LOCAL_PORT/v1"
fi
MODEL_LEAF=""
if [ -n "$SPOTLIGHT_MODEL_REPO" ]; then
  MODEL_LEAF="${SPOTLIGHT_MODEL_REPO##*/}"
fi

# === Colors + spinner + step headers (verbatim from buildScript) ===
_c_reset=$'\033[0m'; _c_cyan=$'\033[36m'; _c_green=$'\033[32m'; _c_red=$'\033[31m'; _c_yellow=$'\033[33m'; _c_dim=$'\033[2m'; _c_bold=$'\033[1m'
_spin_frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)

spin() {
  local msg="$1"; shift
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: %s — %s\n' "$msg" "$*"
    return 0
  fi
  local tmpfile; tmpfile="$(mktemp)"
  ( "$@" ) >"$tmpfile" 2>&1 &
  local pid=$!
  local i=0 n=${#_spin_frames[@]}
  printf "\033[?25l" 2>/dev/null || true
  while kill -0 $pid 2>/dev/null; do
    printf "\r\033[K%s%s%s %s" "$_c_cyan" "${_spin_frames[$((i % n))]}" "$_c_reset" "$msg"
    i=$((i + 1))
    sleep 0.08
  done
  wait $pid 2>/dev/null; local status=$?
  printf "\033[?25h" 2>/dev/null || true
  if [ $status -eq 0 ]; then
    printf "\r\033[K%s✓%s %s\n" "$_c_green" "$_c_reset" "$msg"
  else
    printf "\r\033[K%s✗%s %s\n" "$_c_red" "$_c_reset" "$msg"
    echo "$_c_dim─── output ───$_c_reset"
    cat "$tmpfile"
  fi
  rm -f "$tmpfile"
  return $status
}

step() { printf "\n%s%s━━ %s ━━%s\n" "$_c_bold" "$_c_cyan" "$1" "$_c_reset"; }

# Skill placement contract (engine docs/skill-placement-contract.md): the
# canonical store for EVERY runtime is ~/.agents/skills/spotlight/<id> -> the
# checkout; runtimes with their own skills dir (opencode, pi, claude) get ONE
# product-level adapter symlink <runtime skills dir>/spotlight -> the canonical
# namespace. skills.manifest (generated by `bsig skills vendor`) lists exactly
# the set; fall back to the on-disk skill dirs if it is absent.
SPOTLIGHT_CANONICAL_SKILLS="$HOME/.agents/skills/spotlight"

# Per-skill links into <dest>. Used for the canonical store and as the legacy
# fallback when an adapter dir cannot be migrated.
link_spotlight_skills() {
  local _dest="$1" _sid skill_dir
  if [ -s "$SPOTLIGHT_DIR/skills.manifest" ]; then
    while IFS= read -r _sid; do
      { [ -n "$_sid" ] && [ -d "$SPOTLIGHT_DIR/skills/$_sid" ]; } || continue
      ln -sfn "$SPOTLIGHT_DIR/skills/$_sid" "$_dest/$_sid"
    done < "$SPOTLIGHT_DIR/skills.manifest"
  else
    for skill_dir in "$SPOTLIGHT_DIR/skills/"*/; do
      [ -d "$skill_dir" ] || continue
      ln -sfn "$skill_dir" "$_dest/$(basename "$skill_dir")"
    done
  fi
}

place_spotlight_skills_canonical() {
  run mkdir -p "$SPOTLIGHT_CANONICAL_SKILLS"
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: link manifest skills into %s\n' "$SPOTLIGHT_CANONICAL_SKILLS"
    return 0
  fi
  link_spotlight_skills "$SPOTLIGHT_CANONICAL_SKILLS"
}

# One product-level adapter symlink <adapter> -> canonical store. Migrates a
# legacy per-skill directory in place (removes only symlinks); a dir holding
# real files falls back to per-skill links — never deletes user data.
link_spotlight_adapter() {
  local _adapter="$1" _entry
  place_spotlight_skills_canonical
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: %s -> %s\n' "$_adapter" "$SPOTLIGHT_CANONICAL_SKILLS"
    return 0
  fi
  mkdir -p "$(dirname "$_adapter")"
  if [ -d "$_adapter" ] && [ ! -L "$_adapter" ]; then
    for _entry in "$_adapter"/*; do
      [ -L "$_entry" ] && rm "$_entry"
    done
    if ! rmdir "$_adapter" 2>/dev/null; then
      printf "%s!%s %s holds non-Spotlight files; leaving per-skill links in place\n" "$_c_yellow" "$_c_reset" "$_adapter"
      link_spotlight_skills "$_adapter"
      return 0
    fi
  fi
  ln -sfn "$SPOTLIGHT_CANONICAL_SKILLS" "$_adapter"
}

echo ""
echo "${_c_bold}${_c_cyan}  ╔════════════════════════════════════════════════╗${_c_reset}"
echo "${_c_bold}${_c_cyan}  ║           Spotlight installer                  ║${_c_reset}"
echo "${_c_bold}${_c_cyan}  ╚════════════════════════════════════════════════╝${_c_reset}"
echo ""

OS="$(uname -s)"
if [ "$OS" != "Darwin" ] && [ "$OS" != "Linux" ]; then
  echo "Unsupported OS: $OS. macOS or Linux required (Windows: use WSL)." >&2
  exit 1
fi

step "Prerequisites"

ensure_brew() {
  if command -v brew >/dev/null 2>&1; then
    printf "%s✓%s Homebrew present\n" "$_c_green" "$_c_reset"; return 0
  fi
  # Dry-run must never prompt or fetch remote code — the $(curl …) below
  # executes eagerly even when the outer command is wrapped by run().
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: install Homebrew (consent prompt + https://brew.sh install script)\n'
    return 0
  fi
  echo ""
  echo "Homebrew is needed to install other tools. Install it now? [Y/n]"
  read -r ans </dev/tty || ans="Y"
  if [[ "$ans" =~ ^[Nn] ]]; then echo "Aborted." >&2; exit 1; fi
  run /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
  [ -x /usr/local/bin/brew ] && eval "$(/usr/local/bin/brew shellenv)"
  [ -x /home/linuxbrew/.linuxbrew/bin/brew ] && eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
  # The && probes above leave a non-zero status when brew landed elsewhere;
  # under set -e that status must not kill the install.
  return 0
}

ensure_tool() {
  local cmd="$1"; local pkg="${2:-$1}"
  if command -v "$cmd" >/dev/null 2>&1; then
    printf "%s✓%s %s present\n" "$_c_green" "$_c_reset" "$cmd"; return 0
  fi
  ensure_brew
  spin "Installing $cmd via brew" brew install "$pkg"
}

reviewed_npm_version() {
  case "$1" in
    firecrawl-cli) echo "$FIRECRAWL_CLI_VERSION" ;;
    @tobilu/qmd) echo "$QMD_VERSION" ;;
    dev-browser) echo "$DEV_BROWSER_VERSION" ;;
    @anthropic-ai/claude-code) echo "$CLAUDE_CODE_VERSION" ;;
    @google/gemini-cli) echo "$GEMINI_CLI_VERSION" ;;
    @openai/codex) echo "$OPENAI_CODEX_VERSION" ;;
    opencode-ai) echo "$OPENCODE_AI_VERSION" ;;
    @earendil-works/pi-coding-agent) echo "$PI_CODING_AGENT_VERSION" ;;
    *) return 1 ;;
  esac
}

npm_global_version() {
  local package="$1" prefix="${2:-}"
  if [ -n "$prefix" ]; then
    npm root -g --prefix "$prefix" >/tmp/spotlight-npm-root.$$ 2>/dev/null || return 1
  else
    npm root -g >/tmp/spotlight-npm-root.$$ 2>/dev/null || return 1
  fi
  local npm_root; npm_root="$(cat /tmp/spotlight-npm-root.$$)"
  rm -f /tmp/spotlight-npm-root.$$
  node - "$npm_root" "$package" <<'NODE'
const fs = require("fs");
const path = require("path");
const root = process.argv[2];
const pkg = process.argv[3];
const parts = pkg.startsWith("@") ? pkg.split("/") : [pkg];
const manifest = path.join(root, ...parts, "package.json");
try {
  const data = JSON.parse(fs.readFileSync(manifest, "utf8"));
  process.stdout.write(data.version);
} catch {
  process.exit(1);
}
NODE
}

verify_npm_global_exact() {
  local binary="$1" package="$2" expected="$3" prefix="${4:-}" actual=""
  command -v "$binary" >/dev/null 2>&1 || {
    echo "$binary missing after installing $package@$expected" >&2
    return 1
  }
  actual="$(npm_global_version "$package" "$prefix" 2>/dev/null || true)"
  if [ "$actual" != "$expected" ]; then
    echo "$package version mismatch: expected $expected, found ${actual:-unknown}" >&2
    return 1
  fi
}

ensure_npm_global_exact() {
  local binary="$1" package="$2" prefix="${3:-}" version install_spec installed=""
  version="$(reviewed_npm_version "$package")" || {
    echo "No reviewed npm version pin for $package. Refusing to install." >&2
    exit 1
  }
  install_spec="$package@$version"
  installed="$(npm_global_version "$package" "$prefix" 2>/dev/null || true)"
  if command -v "$binary" >/dev/null 2>&1 && [ "$installed" = "$version" ]; then
    printf "%s✓%s %s already installed at reviewed version %s\n" "$_c_green" "$_c_reset" "$binary" "$version"
    return 0
  fi
  if [ -n "$installed" ] && [ "$installed" != "$version" ]; then
    printf "%s!%s %s is installed at %s; replacing with reviewed %s\n" "$_c_yellow" "$_c_reset" "$package" "$installed" "$version"
  fi
  if [ -n "$prefix" ]; then
    spin "Installing $install_spec" npm install -g --prefix "$prefix" "$install_spec"
    export PATH="$prefix/bin:$PATH"
  else
    spin "Installing $install_spec" npm install -g "$install_spec"
  fi
  if [ "$DRY_RUN" != "1" ]; then
    verify_npm_global_exact "$binary" "$package" "$version" "$prefix"
  fi
}

install_python_reviewed_deps() {
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: python3 -m pip install --user --quiet jsonschema==%s requests==%s\n' "$JSONSCHEMA_VERSION" "$REQUESTS_VERSION"
    return 0
  fi
  spin "Installing reviewed Python deps" python3 -m pip install --user --quiet \
    "jsonschema==$JSONSCHEMA_VERSION" \
    "requests==$REQUESTS_VERSION"
  python3 - "$JSONSCHEMA_VERSION" "$REQUESTS_VERSION" <<'PY'
import importlib.metadata as metadata
import sys

expected = {
    "jsonschema": sys.argv[1],
    "requests": sys.argv[2],
}
for package, version in expected.items():
    actual = metadata.version(package)
    if actual != version:
        raise SystemExit(f"{package} version mismatch: expected {version}, found {actual}")
PY
}

update_repo_ff_only() {
  local dir="$1" name="$2" branch="main"
  [ -d "$dir/.git" ] || return 1
  (
    cd "$dir"
    if ! git diff --quiet || ! git diff --cached --quiet; then
      echo "$name has local uncommitted changes; skipping automatic update."
      return 0
    fi
    before="$(git rev-parse HEAD)"
    git fetch origin "$branch"
    if git merge-base --is-ancestor HEAD "origin/$branch"; then
      git merge --ff-only "origin/$branch"
      after="$(git rev-parse HEAD)"
      echo "$name $before -> $after"
    else
      echo "$name has local commits or divergent history; skipping automatic update."
    fi
  )
}

if ! command -v git >/dev/null 2>&1; then
  if [ "$OS" = "Darwin" ]; then
    echo "git is not installed. Install Xcode Command Line Tools now? [Y/n]"
    read -r ans </dev/tty || ans="Y"
    if [[ "$ans" =~ ^[Nn] ]]; then echo "Aborted." >&2; exit 1; fi
    run xcode-select --install || true
    echo "A dialog opened. Complete the install, then re-run this script."
    exit 0
  else
    ensure_tool git
  fi
else
  printf "%s✓%s git present\n" "$_c_green" "$_c_reset"
fi

ensure_tool node
ensure_tool python3 python@3.12
if [ "$SPOTLIGHT_MODE" = "local" ]; then
  ensure_brew  # local mode needs brew for the inference server + agent
fi

if [ "$SPOTLIGHT_VAULT_APP" = "tolaria" ]; then
  step "Tolaria vault"
  if [ "$OS" = "Darwin" ]; then
    if [ -d "/Applications/Tolaria.app" ] || [ -d "$HOME/Applications/Tolaria.app" ]; then
      printf "%s✓%s Tolaria.app installed\n" "$_c_green" "$_c_reset"
    else
      if [ "$DRY_RUN" = "1" ]; then
        printf "DRY-RUN: Tolaria.app missing; installer would ask for a reviewed manual install before continuing.\n"
      else
        echo "Tolaria.app is not installed. Spotlight no longer downloads Tolaria from a moving latest-release URL." >&2
        echo "Install a reviewed Tolaria build manually, then re-run setup; or choose Obsidian/local directory mode." >&2
        exit 1
      fi
    fi
    run open -a Tolaria 2>/dev/null || run open "$HOME/Applications/Tolaria.app" 2>/dev/null || true
  else
    echo "Tolaria selected. On Linux, install a reviewed Tolaria build manually; Spotlight will still write Markdown files to the vault path."
  fi
else
  step "Obsidian vault"
  if [ -d "/Applications/Obsidian.app" ] || [ -d "$HOME/Applications/Obsidian.app" ]; then
    printf "%s✓%s Obsidian.app installed\n" "$_c_green" "$_c_reset"
  else
    ensure_brew
    spin "Installing Obsidian via brew cask" brew install --cask obsidian
  fi

  if ! command -v obsidian >/dev/null 2>&1; then
    printf "%s!%s Opening Obsidian so you can enable the CLI\n" "$_c_cyan" "$_c_reset"
    run open -a Obsidian 2>/dev/null || true
    echo ""
    echo "  ${_c_bold}Enable the Obsidian CLI (one-time):${_c_reset}"
    echo "    Settings → General → Advanced → toggle ${_c_bold}Command Line Interface${_c_reset} ON"
    echo ""
    echo "  The first time you run ${_c_bold}spotlight${_c_reset}, the preflight check will detect"
    echo "  whether the CLI is enabled and prompt you again if needed. You can continue"
    echo "  the rest of this installer now while Obsidian is open."
    echo ""
  else
    printf "%s✓%s obsidian CLI already on PATH\n" "$_c_green" "$_c_reset"
  fi
fi

step "Spotlight repo"
if [ -d "$SPOTLIGHT_DIR/.git" ]; then
  spin "Updating Spotlight at $SPOTLIGHT_DIR" update_repo_ff_only "$SPOTLIGHT_DIR" "Spotlight"
else
  spin "Cloning Spotlight to $SPOTLIGHT_DIR" git clone "$REPO_URL" "$SPOTLIGHT_DIR"
fi
cd "$SPOTLIGHT_DIR"

step "Core dependencies"
ensure_npm_global_exact firecrawl firecrawl-cli
ensure_npm_global_exact qmd @tobilu/qmd

# =====================================================================
# LOCAL MODE — inference server + agent harness
# =====================================================================
if [ "$SPOTLIGHT_MODE" = "local" ]; then

  # ---- Inference server (llama-server OR Ollama) ----
  if [ "$SPOTLIGHT_LOCAL_SERVER" = "llamacpp" ]; then
    step "Local inference (llama-server)"
    if ! command -v llama-server >/dev/null 2>&1; then
      spin "Installing llama.cpp via brew" brew install llama.cpp
    else
      printf "%s✓%s llama.cpp already installed\n" "$_c_green" "$_c_reset"
    fi
    MODEL_DIR="$HOME/Models/$MODEL_LEAF"
    run mkdir -p "$MODEL_DIR"
    if [ ! -f "$MODEL_DIR/$GGUF_FILE" ]; then
      # Download to .part and rename on success: an interrupted download is
      # resumed (--continue-at -) instead of being mistaken for a complete file.
      spin "Downloading $GGUF_FILE from huggingface.co/$SPOTLIGHT_MODEL_REPO" \
        curl -L --fail --retry 3 --continue-at - \
          "https://huggingface.co/$SPOTLIGHT_MODEL_REPO/resolve/main/$GGUF_FILE" \
          -o "$MODEL_DIR/$GGUF_FILE.part"
      run mv "$MODEL_DIR/$GGUF_FILE.part" "$MODEL_DIR/$GGUF_FILE"
    else
      printf "%s✓%s Model already downloaded at %s\n" "$_c_green" "$_c_reset" "$MODEL_DIR/$GGUF_FILE"
    fi
  else
    step "Local inference (Ollama)"
    if ! command -v ollama >/dev/null 2>&1; then
      spin "Installing Ollama via brew" brew install ollama
      run brew services start ollama 2>/dev/null || true
      [ "$DRY_RUN" = "1" ] || sleep 2
    else
      printf "%s✓%s Ollama already installed\n" "$_c_green" "$_c_reset"
    fi

    step "Journalism model download ($OLLAMA_SIZE_LABEL)"
    echo "Ollama will stream download progress below — grab a coffee, 5–15 min on a typical connection."
    echo ""
    OLLAMA_MODEL="$OLLAMA_MODEL_DEFAULT"
    SPOTLIGHT_OLLAMA_ALIAS="$OLLAMA_ALIAS_DEFAULT"
    if [ "$DRY_RUN" != "1" ] && ! ollama show "$SPOTLIGHT_OLLAMA_ALIAS" >/dev/null 2>&1; then
      if ! ollama show "$OLLAMA_MODEL" >/dev/null 2>&1; then
        ollama pull "$OLLAMA_MODEL" || {
          printf "%s✗%s Ollama could not pull %s.\n" "$_c_red" "$_c_reset" "$OLLAMA_MODEL"
          printf "   Edit %s/.env after install or re-run with a working OLLAMA_MODEL.\n" "$SPOTLIGHT_DIR"
          printf "   Example: OLLAMA_MODEL=gemma3:27b SPOTLIGHT_OLLAMA_ALIAS=spotlight-local\n"
          exit 1
        }
      fi
      TMP_MODELFILE="$(mktemp)"
      printf "FROM %s\n" "$OLLAMA_MODEL" > "$TMP_MODELFILE"
      # Qwen 3.6 27B runs in thinking mode — DO NOT inject /no_think.
      #
      # Earlier revisions overrode the chat TEMPLATE to inject /no_think
      # into every user message, on the theory that the abliterated 27B
      # ignores `think:false` and otherwise burns ~10k reasoning tokens.
      # That premise is correct in mechanism but wrong in consequence:
      # the abliterated Qwen 3.6 family's /no_think codepath is damaged
      # (verified empirically on Huihui base + Tom's journalist tune at
      # Q4_K_M with Qwen-recommended sampling — output collapses to
      # multilingual token soup and lock-loops within ~200 tokens).
      # Probable cause: abliteration calibration covered only the
      # thinking codepath, leaving the no-think branch broken.
      #
      # We keep the model's native chat template (thinking on) and
      # rely on opencode's `limit.output: 16384` to give enough budget
      # for reasoning + content. Expect ~3-5× slower per-prompt vs 9B.
      #
      # Stop-token workaround: the abliterated journalist fine-tune emits
      # <|endoftext|> at the end of assistant turns rather than the
      # chat-template's <|im_end|>. Ollama's auto-derived stop list reads
      # tokenizer.ggml.eos_token_id from the GGUF (= <|im_end|>) and does
      # NOT include <|endoftext|>, so without these explicit stops the
      # model rambles past its own end-of-turn into a fake user message.
      # Adding both is belt-and-braces (verified: finish_reason flips
      # from "length" to "stop" with these in place).
      case "$SPOTLIGHT_LOCAL_MODEL" in
        qwen27b)
          # The abliterated Qwen journalist tune emits <|endoftext|> at end of
          # turn instead of <|im_end|>; Ollama's auto stop list misses it, so
          # add both. Gemma-4 needs neither (clean stop tokens, no thinking).
          printf 'PARAMETER stop "<|im_end|>"\n' >> "$TMP_MODELFILE"
          printf 'PARAMETER stop "<|endoftext|>"\n' >> "$TMP_MODELFILE"
          ;;
      esac
      ollama create "$SPOTLIGHT_OLLAMA_ALIAS" -f "$TMP_MODELFILE"
      rm -f "$TMP_MODELFILE"
    elif [ "$DRY_RUN" = "1" ]; then
      printf 'DRY-RUN: ollama pull %s + create alias %s\n' "$OLLAMA_MODEL" "$SPOTLIGHT_OLLAMA_ALIAS"
    else
      printf "%s✓%s Ollama alias %s already exists\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_OLLAMA_ALIAS"
    fi
  fi

  # ---- Agent harness: opencode OR pi ----
  if [ "$SPOTLIGHT_AGENT" = "pi" ]; then
    # Pi has no native sub-agents. Warn before installing.
    echo ""
    printf "%s⚠ Pi has no native sub-agents.%s\n" "$_c_red" "$_c_reset"
    echo "  Spotlight's investigator and fact-checker will share one context,"
    echo "  weakening the verification independence guarantee."
    echo "  opencode is the recommended agent for full Spotlight semantics."
    echo ""
    if [ "$DRY_RUN" != "1" ]; then
      echo "  Continue with Pi anyway? [y/N]"
      read -r ans </dev/tty || ans="N"
      if [[ ! "$ans" =~ ^[Yy] ]]; then
        echo "Aborted. Re-run setup.html and pick opencode for the recommended setup."
        exit 1
      fi
    fi

    step "Agent harness (Pi)"
    ensure_npm_global_exact pi @earendil-works/pi-coding-agent

    step "pi-llama-cpp extension (model browser for llama-server)"
    spin "Installing pi-llama-cpp" pi install npm:pi-llama-cpp

    # Symlink Spotlight skills into pi's global skill dir, namespaced under spotlight/
    # (engine-matching <root>/<product>/<skill> shape; pi recurses to discover them).
    step "Spotlight skills → pi"
    link_spotlight_adapter "$HOME/.pi/agent/skills/spotlight"
    [ "$DRY_RUN" = "1" ] || printf "%s✓%s pi loads Spotlight skills via ~/.pi/agent/skills/spotlight → %s\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_CANONICAL_SKILLS"

    # Tell pi where the OpenAI-compatible endpoint is.
    step "Pi inference endpoint config"
    PI_SETTINGS="$HOME/.pi/agent/settings.json"
    run mkdir -p "$(dirname "$PI_SETTINGS")"
    if [ "$DRY_RUN" = "1" ]; then
      printf 'DRY-RUN: write %s with llamaServerUrl=http://127.0.0.1:%s\n' "$PI_SETTINGS" "$LOCAL_PORT"
    else
      if ! command -v jq >/dev/null 2>&1; then spin "Installing jq via brew" brew install jq; fi
      [ -f "$PI_SETTINGS" ] || echo '{}' > "$PI_SETTINGS"
      TMP=$(mktemp)
      jq --arg url "http://127.0.0.1:$LOCAL_PORT" '.llamaServerUrl = $url' "$PI_SETTINGS" > "$TMP" && mv "$TMP" "$PI_SETTINGS"
      printf "%s✓%s Pi llamaServerUrl set to http://127.0.0.1:%s\n" "$_c_green" "$_c_reset" "$LOCAL_PORT"
    fi

  else
    # opencode (default)
    if [ "$SPOTLIGHT_OPENCODE_INTERFACE" = "desktop" ]; then
      step "Agent harness (opencode Desktop)"
      if [ ! -d "/Applications/opencode.app" ] && [ ! -d "$HOME/Applications/opencode.app" ]; then
        spin "Installing opencode-desktop via brew cask" brew install --cask opencode-desktop
      else
        printf "%s✓%s opencode-desktop already installed\n" "$_c_green" "$_c_reset"
      fi
      # CLI is needed too for the launcher script and skill registration
    fi
    step "Agent harness (opencode CLI)"
    if ! command -v opencode >/dev/null 2>&1; then
      spin "Installing opencode via brew" brew install opencode || \
        bash -c 'curl -fsSL https://opencode.ai/install | bash'
    else
      printf "%s✓%s opencode already installed\n" "$_c_green" "$_c_reset"
    fi

    step "Spotlight skills → opencode"
    link_spotlight_adapter "$HOME/.config/opencode/skills/spotlight"
    [ "$DRY_RUN" = "1" ] || printf "%s✓%s opencode loads Spotlight skills via ~/.config/opencode/skills/spotlight → %s\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_CANONICAL_SKILLS"

    step "opencode provider config"
    OC_CFG="$HOME/.config/opencode/opencode.json"
    run mkdir -p "$(dirname "$OC_CFG")"
    if [ "$DRY_RUN" = "1" ]; then
      printf 'DRY-RUN: merge %s provider into %s with baseURL=%s\n' "$SPOTLIGHT_LOCAL_SERVER" "$OC_CFG" "$LOCAL_BASE_URL"
    else
      [ -f "$OC_CFG" ] || echo '{"$schema":"https://opencode.ai/config.json","provider":{}}' > "$OC_CFG"
      if ! command -v jq >/dev/null 2>&1; then spin "Installing jq via brew" brew install jq; fi
      TMP=$(mktemp)
      if [ "$SPOTLIGHT_LOCAL_SERVER" = "llamacpp" ]; then
        jq --arg base "$LOCAL_BASE_URL" --arg id "qwen27" --arg name "Qwen3.6-27B Uncensored (local llama.cpp)" \
          '.provider["llama.cpp"] = {"npm":"@ai-sdk/openai-compatible","name":"llama-server (local)","options":{"baseURL":$base},"models":{($id):{"name":$name}}}' \
          "$OC_CFG" > "$TMP" && mv "$TMP" "$OC_CFG"
      else
        # Per-model `limit.output` for the 27B because Qwen 3.6 thinking
        # burns ~10k tokens of reasoning even with /no_think injected;
        # without a generous output budget, content comes back empty.
        # The 9B doesn't need this (no thinking mode), so we leave its
        # limit unset and inherit opencode's default.
        if [ "$SPOTLIGHT_LOCAL_MODEL" = "qwen27b" ]; then
          jq --arg base "$LOCAL_BASE_URL" --arg id "$OLLAMA_ALIAS_DEFAULT" --arg name "$MODEL_LEAF (local Ollama)" \
            '.provider["ollama"] = {"npm":"@ai-sdk/openai-compatible","name":"Ollama (local OpenAI-compatible)","options":{"baseURL":$base},"models":{($id):{"name":$name,"limit":{"output":16384}}}}' \
            "$OC_CFG" > "$TMP" && mv "$TMP" "$OC_CFG"
        else
          jq --arg base "$LOCAL_BASE_URL" --arg id "$OLLAMA_ALIAS_DEFAULT" --arg name "$MODEL_LEAF (local Ollama)" \
            '.provider["ollama"] = {"npm":"@ai-sdk/openai-compatible","name":"Ollama (local OpenAI-compatible)","options":{"baseURL":$base},"models":{($id):{"name":$name}}}' \
            "$OC_CFG" > "$TMP" && mv "$TMP" "$OC_CFG"
        fi
      fi
      printf "%s✓%s opencode.json updated with %s provider\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_LOCAL_SERVER"
    fi
  fi

  # ---- Local launcher script ----
  step "Spotlight local launcher"
  run mkdir -p "$HOME/.local/bin"
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: write ~/.local/bin/spotlight-local for %s/%s\n' "$SPOTLIGHT_LOCAL_SERVER" "$SPOTLIGHT_AGENT"
  else
    if [ "$SPOTLIGHT_LOCAL_SERVER" = "llamacpp" ]; then
      if [ "$SPOTLIGHT_AGENT" = "pi" ]; then
        cat > "$HOME/.local/bin/spotlight-local" <<LAUNCHER_EOF
#!/usr/bin/env bash
# Start llama-server + pi for Spotlight investigations.
set -euo pipefail
MODEL="\$HOME/Models/$MODEL_LEAF/$GGUF_FILE"
command -v llama-server >/dev/null 2>&1 || { echo "llama-server missing — brew install llama.cpp" >&2; exit 1; }
command -v pi           >/dev/null 2>&1 || { echo "pi missing — install reviewed @earendil-works/pi-coding-agent@$PI_CODING_AGENT_VERSION with install-spotlight.sh" >&2; exit 1; }
[ -f "\$MODEL" ] || { echo "Model not found: \$MODEL" >&2; exit 1; }
lsof -ti:8080 >/dev/null 2>&1 && { echo "Port 8080 already in use — kill the existing process first" >&2; exit 1; }
llama-server --model "\$MODEL" --alias qwen27 --host 127.0.0.1 --port 8080 \\
  --ctx-size 65536 --n-gpu-layers 999 --jinja --flash-attn on >/tmp/llama-server-pi.log 2>&1 &
SERVER_PID=\$!
cleanup() { kill -TERM "\$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM HUP
echo -n "Waiting for llama-server"
READY=0
for i in {1..120}; do curl -sf http://127.0.0.1:8080/v1/models >/dev/null && { echo " ready."; READY=1; break; }; echo -n "."; sleep 1; done
[ "\$READY" = "1" ] || { echo " llama-server did not become ready after 120s — see /tmp/llama-server-*.log" >&2; exit 1; }
pi "\$@"
LAUNCHER_EOF
      else
        cat > "$HOME/.local/bin/spotlight-local" <<LAUNCHER_EOF
#!/usr/bin/env bash
# Start llama-server + opencode for Spotlight investigations.
set -euo pipefail
MODEL="\$HOME/Models/$MODEL_LEAF/$GGUF_FILE"
command -v llama-server >/dev/null 2>&1 || { echo "llama-server missing — brew install llama.cpp" >&2; exit 1; }
command -v opencode     >/dev/null 2>&1 || { echo "opencode missing — brew install opencode" >&2; exit 1; }
[ -f "\$MODEL" ] || { echo "Model not found: \$MODEL" >&2; exit 1; }
lsof -ti:8080 >/dev/null 2>&1 && { echo "Port 8080 already in use — kill the existing process first" >&2; exit 1; }
llama-server --model "\$MODEL" --alias qwen27 --host 127.0.0.1 --port 8080 \\
  --ctx-size 65536 --n-gpu-layers 999 --jinja --flash-attn on >/tmp/llama-server-opencode.log 2>&1 &
SERVER_PID=\$!
cleanup() { kill -TERM "\$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM HUP
echo -n "Waiting for llama-server"
READY=0
for i in {1..120}; do curl -sf http://127.0.0.1:8080/v1/models >/dev/null && { echo " ready."; READY=1; break; }; echo -n "."; sleep 1; done
[ "\$READY" = "1" ] || { echo " llama-server did not become ready after 120s — see /tmp/llama-server-*.log" >&2; exit 1; }
opencode --model llama.cpp/qwen27 "\$@"
LAUNCHER_EOF
      fi
    else
      # Ollama branch
      if [ "$SPOTLIGHT_AGENT" = "pi" ]; then
        cat > "$HOME/.local/bin/spotlight-local" <<LAUNCHER_EOF
#!/usr/bin/env bash
# Start Ollama + pi for Spotlight investigations.
set -euo pipefail
SPOTLIGHT_DIR="\${SPOTLIGHT_DIR:-$SPOTLIGHT_DIR}"
ENV_FILE="\$SPOTLIGHT_DIR/.env"
if [ -f "\$ENV_FILE" ]; then set -a; . "\$ENV_FILE"; set +a; fi
command -v ollama >/dev/null 2>&1 || { echo "ollama missing — brew install ollama" >&2; exit 1; }
command -v pi     >/dev/null 2>&1 || { echo "pi missing — install reviewed @earendil-works/pi-coding-agent@$PI_CODING_AGENT_VERSION with install-spotlight.sh" >&2; exit 1; }
OLLAMA_MODEL="\${OLLAMA_MODEL:-$OLLAMA_MODEL_DEFAULT}"
SPOTLIGHT_OLLAMA_ALIAS="\${SPOTLIGHT_OLLAMA_ALIAS:-$OLLAMA_ALIAS_DEFAULT}"
ollama list >/dev/null 2>&1 || { brew services start ollama 2>/dev/null || ollama serve >/tmp/ollama-spotlight.log 2>&1 & sleep 2; }
if ! ollama show "\$SPOTLIGHT_OLLAMA_ALIAS" >/dev/null 2>&1; then
  ollama show "\$OLLAMA_MODEL" >/dev/null 2>&1 || ollama pull "\$OLLAMA_MODEL"
  TMP_MODELFILE="\$(mktemp)"
  printf "FROM %s\\n" "\$OLLAMA_MODEL" > "\$TMP_MODELFILE"
  ollama create "\$SPOTLIGHT_OLLAMA_ALIAS" -f "\$TMP_MODELFILE"
  rm -f "\$TMP_MODELFILE"
fi
pi "\$@"
LAUNCHER_EOF
      else
        cat > "$HOME/.local/bin/spotlight-local" <<LAUNCHER_EOF
#!/usr/bin/env bash
# Start Ollama + opencode for Spotlight investigations.
set -euo pipefail
expand_path() {
  local input="\$1"
  if [ "\$input" = "~" ]; then printf "%s\\n" "\$HOME"
  elif [[ "\$input" == "~/"* ]]; then printf "%s/%s\\n" "\$HOME" "\${input#~/}"
  else printf "%s\\n" "\$input"; fi
}
SPOTLIGHT_DIR_DEFAULT_INPUT='$SPOTLIGHT_DIR_INPUT'
SPOTLIGHT_DIR_DEFAULT="\$(expand_path "\$SPOTLIGHT_DIR_DEFAULT_INPUT")"
SPOTLIGHT_DIR="\${SPOTLIGHT_DIR:-\$SPOTLIGHT_DIR_DEFAULT}"
ENV_FILE="\$SPOTLIGHT_DIR/.env"
if [ -f "\$ENV_FILE" ]; then set -a; . "\$ENV_FILE"; set +a; fi
command -v ollama   >/dev/null 2>&1 || { echo "ollama missing — brew install ollama" >&2; exit 1; }
command -v opencode >/dev/null 2>&1 || { echo "opencode missing — brew install opencode" >&2; exit 1; }
OLLAMA_MODEL="\${OLLAMA_MODEL:-$OLLAMA_MODEL_DEFAULT}"
SPOTLIGHT_OLLAMA_ALIAS="\${SPOTLIGHT_OLLAMA_ALIAS:-$OLLAMA_ALIAS_DEFAULT}"
ollama list >/dev/null 2>&1 || { brew services start ollama 2>/dev/null || ollama serve >/tmp/ollama-spotlight.log 2>&1 & sleep 2; }
if ! ollama show "\$SPOTLIGHT_OLLAMA_ALIAS" >/dev/null 2>&1; then
  if ! ollama show "\$OLLAMA_MODEL" >/dev/null 2>&1; then
    ollama pull "\$OLLAMA_MODEL" || { echo "Could not pull \$OLLAMA_MODEL. Set OLLAMA_MODEL in \$ENV_FILE to a working Ollama model." >&2; exit 1; }
  fi
  TMP_MODELFILE="\$(mktemp)"
  printf "FROM %s\\n" "\$OLLAMA_MODEL" > "\$TMP_MODELFILE"
  ollama create "\$SPOTLIGHT_OLLAMA_ALIAS" -f "\$TMP_MODELFILE"
  rm -f "\$TMP_MODELFILE"
fi
opencode --model "ollama/\$SPOTLIGHT_OLLAMA_ALIAS" "\$@"
LAUNCHER_EOF
      fi
    fi
    chmod +x "$HOME/.local/bin/spotlight-local"
    printf "%s✓%s Launcher installed at ~/.local/bin/spotlight-local\n" "$_c_green" "$_c_reset"
  fi

# =====================================================================
# CLOUD MODE — pick a hosted runtime
# =====================================================================
else
  # SPOTLIGHT_RUNTIME ∈ {claude, gemini, codex, opencode}
  case "$SPOTLIGHT_RUNTIME" in
    claude)   RT_BIN=claude;   RT_PKG="@anthropic-ai/claude-code"; RT_CTX="CLAUDE.md";  RT_NAME="Claude Code" ;;
    gemini)   RT_BIN=gemini;   RT_PKG="@google/gemini-cli";        RT_CTX="GEMINI.md";  RT_NAME="Gemini" ;;
    codex)    RT_BIN=codex;    RT_PKG="@openai/codex";             RT_CTX="";           RT_NAME="Codex" ;;
    opencode) RT_BIN=opencode; RT_PKG="opencode-ai";               RT_CTX="";           RT_NAME="OpenCode" ;;
    *) echo "Unknown SPOTLIGHT_RUNTIME: $SPOTLIGHT_RUNTIME" >&2; exit 1 ;;
  esac

  RT_LABEL="$RT_NAME"
  [ "$SPOTLIGHT_RUNTIME" = "opencode" ] && [ -n "$SPOTLIGHT_OPENCODE_PROVIDER" ] && \
    RT_LABEL="OpenCode (provider: $SPOTLIGHT_OPENCODE_PROVIDER)"

  step "$RT_LABEL"
  if ! command -v "$RT_BIN" >/dev/null 2>&1; then
    if [ "$SPOTLIGHT_RUNTIME" = "gemini" ]; then
      NPM_PREFIX="$HOME/.npm-global"; run mkdir -p "$NPM_PREFIX"
      ensure_npm_global_exact "$RT_BIN" "$RT_PKG" "$NPM_PREFIX"
    else
      ensure_npm_global_exact "$RT_BIN" "$RT_PKG"
    fi
  else
    if [ "$SPOTLIGHT_RUNTIME" = "gemini" ]; then
      NPM_PREFIX="$HOME/.npm-global"
      ensure_npm_global_exact "$RT_BIN" "$RT_PKG" "$NPM_PREFIX"
    else
      ensure_npm_global_exact "$RT_BIN" "$RT_PKG"
    fi
  fi
  if [ -n "$RT_CTX" ]; then
    run ln -sfn "$SPOTLIGHT_DIR/AGENTS.md" "$SPOTLIGHT_DIR/$RT_CTX"
    printf "%s✓%s AGENTS.md linked as %s\n" "$_c_green" "$_c_reset" "$RT_CTX"
  fi

  # Placement contract: every runtime shares the canonical store; Claude Code
  # additionally reads ~/.claude/skills, so it gets the adapter symlink.
  # codex/gemini discover skills via the AGENTS.md contract + in-repo tree.
  step "Spotlight skills → canonical store"
  if [ "$SPOTLIGHT_RUNTIME" = "claude" ]; then
    link_spotlight_adapter "$HOME/.claude/skills/spotlight"
    [ "$DRY_RUN" = "1" ] || printf "%s✓%s Claude Code loads Spotlight skills via ~/.claude/skills/spotlight → %s\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_CANONICAL_SKILLS"
  else
    place_spotlight_skills_canonical
    [ "$DRY_RUN" = "1" ] || printf "%s✓%s Spotlight skills placed at %s\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_CANONICAL_SKILLS"
  fi
  if [ "$SPOTLIGHT_RUNTIME" = "opencode" ]; then
    printf "%s✓%s Provider key will be loaded from .env (%s)\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_CLOUD_KEY_VAR"
    link_spotlight_adapter "$HOME/.config/opencode/skills/spotlight"
    [ "$DRY_RUN" = "1" ] || printf "%s✓%s opencode loads Spotlight skills via ~/.config/opencode/skills/spotlight → %s\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_CANONICAL_SKILLS"

    # Pin the ZDR cloud model for the Fireworks provider: GLM-5.2, served
    # in-house on Fireworks' US GPUs (NOT routed to Z.AI). We write an explicit
    # openai-compatible provider block (key via {env:FIREWORKS_API_KEY}) and set
    # it as the default model, so sensitive investigations use the benchmarked
    # ZDR pick rather than whatever a provider menu defaults to. OpenRouter is
    # left to opencode's own registry (frontier access, not ZDR).
    if [ "$SPOTLIGHT_OPENCODE_PROVIDER" = "fireworks" ]; then
      step "opencode Fireworks/GLM-5.2 pin (ZDR)"
      OC_CFG="$HOME/.config/opencode/opencode.json"
      if [ "$DRY_RUN" = "1" ]; then
        printf 'DRY-RUN: pin fireworks provider + default model accounts/fireworks/models/glm-5p2 into %s\n' "$OC_CFG"
      else
        if ! command -v jq >/dev/null 2>&1; then spin "Installing jq via brew" brew install jq; fi
        run mkdir -p "$(dirname "$OC_CFG")"
        [ -f "$OC_CFG" ] || echo '{"$schema":"https://opencode.ai/config.json","provider":{}}' > "$OC_CFG"
        TMP=$(mktemp)
        jq '.provider["fireworks"] = {"npm":"@ai-sdk/openai-compatible","name":"Fireworks AI (GLM-5.2, ZDR)","options":{"baseURL":"https://api.fireworks.ai/inference/v1","apiKey":"{env:FIREWORKS_API_KEY}"},"models":{"accounts/fireworks/models/glm-5p2":{"name":"GLM-5.2 (Fireworks, ZDR)"}}}
              | .model = "fireworks/accounts/fireworks/models/glm-5p2"' \
          "$OC_CFG" > "$TMP" && mv "$TMP" "$OC_CFG"
        printf "%s✓%s opencode.json pinned to Fireworks · GLM-5.2 (ZDR)\n" "$_c_green" "$_c_reset"
      fi
    fi
  fi
fi

step "Python dependencies"
install_python_reviewed_deps

step "Browser acquisition"
if [ "$SPOTLIGHT_INT_DEVBROWSER" = "true" ]; then
  ensure_npm_global_exact dev-browser dev-browser
  spin "Installing dev-browser Chromium" bash -c "dev-browser install >/dev/null 2>&1 || true"
else
  printf "%s→%s dev-browser not selected; browser acquisition fallback disabled by setup choice\n" "$_c_yellow" "$_c_reset"
fi

# === Write .env ===
echo "→ Writing $SPOTLIGHT_DIR/.env (chmod 600)"
write_env_var() {
  local name="$1" value="$2"
  printf "%s=%q\n" "$name" "$value" >> "$SPOTLIGHT_DIR/.env"
}
if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: write .env with FIRECRAWL_API_KEY, OSINT_NAV_API_KEY, %s + integration keys\n' "${SPOTLIGHT_CLOUD_KEY_VAR:-<none>}"
else
  cat > "$SPOTLIGHT_DIR/.env" <<'ENV_HEADER'
# Spotlight environment — generated by install-spotlight.sh
ENV_HEADER
  write_env_var SPOTLIGHT_DIR "$SPOTLIGHT_DIR"
  write_env_var SPOTLIGHT_VAULT_PATH "$SPOTLIGHT_VAULT_PATH"
  write_env_var SPOTLIGHT_CASES_ROOT "$SPOTLIGHT_CASES_ROOT"
  write_env_var FIRECRAWL_API_KEY "$FIRECRAWL_API_KEY"
  write_env_var OSINT_NAV_API_KEY "$OSINT_NAV_API_KEY"
  if [ -n "$SPOTLIGHT_CLOUD_KEY_VAR" ] && [ -n "$SPOTLIGHT_CLOUD_KEY" ]; then
    write_env_var "$SPOTLIGHT_CLOUD_KEY_VAR" "$SPOTLIGHT_CLOUD_KEY"
  fi
  if [ "$SPOTLIGHT_INT_JUNKIPEDIA" = "true" ] && [ -n "$JUNKIPEDIA_API_KEY" ]; then
    write_env_var JUNKIPEDIA_API_KEY "$JUNKIPEDIA_API_KEY"
  fi
  if [ "$SPOTLIGHT_INT_UNPAYWALL" = "true" ] && [ -n "$UNPAYWALL_EMAIL" ]; then
    write_env_var UNPAYWALL_EMAIL "$UNPAYWALL_EMAIL"
  fi
  if [ "$SPOTLIGHT_INT_RLM" = "true" ]; then
    write_env_var SPOTLIGHT_RLM_MODE "$SPOTLIGHT_RLM_MODE"
    if [ -n "$SPOTLIGHT_RLM_MODEL" ]; then
      write_env_var SPOTLIGHT_RLM_MODEL "$SPOTLIGHT_RLM_MODEL"
    fi
    if [ -n "$SPOTLIGHT_RLM_PREFILTER" ]; then
      write_env_var SPOTLIGHT_RLM_PREFILTER "$SPOTLIGHT_RLM_PREFILTER"
    fi
    if [ -n "$SPOTLIGHT_RLM_HYBRID" ]; then
      write_env_var SPOTLIGHT_RLM_HYBRID "$SPOTLIGHT_RLM_HYBRID"
    fi
  fi
  if [ "$SPOTLIGHT_MODE" = "local" ]; then
    write_env_var MODEL_REPO "$SPOTLIGHT_MODEL_REPO"
    write_env_var LOCAL_SERVER "$SPOTLIGHT_LOCAL_SERVER"
    write_env_var LOCAL_ENDPOINT "$LOCAL_BASE_URL"
    write_env_var SPOTLIGHT_AGENT "$SPOTLIGHT_AGENT"
    if [ "$SPOTLIGHT_LOCAL_SERVER" = "ollama" ]; then
      write_env_var OLLAMA_MODEL "$OLLAMA_MODEL_DEFAULT"
      write_env_var SPOTLIGHT_OLLAMA_ALIAS "$OLLAMA_ALIAS_DEFAULT"
    fi
  fi
  chmod 600 "$SPOTLIGHT_DIR/.env"
fi

# The final $SPOTLIGHT_DIR/.env is canonical from here on — drop the staged
# copy in ~/.config/spotlight so secrets never persist in two places.
# (setup-config.env carries no secrets and is retained for the reuse gate.)
SPOTLIGHT_INSTALL_DONE=1
if [ "$CONFIGURATOR_RAN" = "1" ] && [ "$STAGED_ENV" != "$SPOTLIGHT_DIR/.env" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: delete staged %s once the final .env is written\n' "$STAGED_ENV"
  elif [ -f "$STAGED_ENV" ]; then
    rm -f "$STAGED_ENV"
  fi
fi

step "Writing .spotlight-config.json"
if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: write %s/.spotlight-config.json\n' "$SPOTLIGHT_DIR"
else
  if [ -f "$SPOTLIGHT_DIR/.spotlight-config.json" ]; then
    PREV_VAULT_PATH="$(python3 -c '
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("vault_path", ""))
except Exception:
    pass
' "$SPOTLIGHT_DIR/.spotlight-config.json" 2>/dev/null || true)"
    if [ -n "$PREV_VAULT_PATH" ] && [ "$PREV_VAULT_PATH" != "$SPOTLIGHT_VAULT_PATH" ]; then
      printf "%s!%s Vault path changed from %s; re-pointing qmd at the new vault — old vault data is not migrated.\n" \
        "$_c_yellow" "$_c_reset" "$PREV_VAULT_PATH"
    fi
  fi
  cat > "$SPOTLIGHT_DIR/.spotlight-config.json" <<CONFIG_EOF
{
  "search_library": "firecrawl",
  "vault_path": "$SPOTLIGHT_VAULT_PATH",
  "vault_type": "$([ "$SPOTLIGHT_VAULT_APP" = "tolaria" ] && echo tolaria || echo obsidian)",
  "vault_app": "$SPOTLIGHT_VAULT_APP",
  "case_workspace_root": "$SPOTLIGHT_CASES_ROOT",
  "cases_root": "$SPOTLIGHT_CASES_ROOT",
  "install_path": "$SPOTLIGHT_DIR",
  "mode": "$SPOTLIGHT_MODE",
  "runtime": "$SPOTLIGHT_RUNTIME",
  "local_server": $([ -n "$SPOTLIGHT_LOCAL_SERVER" ] && printf '"%s"' "$SPOTLIGHT_LOCAL_SERVER" || echo null),
  "agent": $([ "$SPOTLIGHT_MODE" = "local" ] && printf '"%s"' "$SPOTLIGHT_AGENT" || echo null),
  "opencode_provider": $([ -n "$SPOTLIGHT_OPENCODE_PROVIDER" ] && printf '"%s"' "$SPOTLIGHT_OPENCODE_PROVIDER" || echo null),
  "integrations": {
    "osint_navigator": {
      "status": "unknown",
      "enabled": true,
      "checked_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
      "source": "setup",
      "required_in_phase_2": false,
      "reason": "preflight not run yet"
    },
    "junkipedia": {
      "enabled": $SPOTLIGHT_INT_JUNKIPEDIA,
      "status": "unknown",
      "source": "setup"
    },
    "dev_browser": {
      "enabled": $SPOTLIGHT_INT_DEVBROWSER,
      "status": "unknown",
      "source": "setup"
    },
    "unpaywall": {
      "enabled": $SPOTLIGHT_INT_UNPAYWALL,
      "status": "unknown",
      "source": "setup"
    },
    "rlm": {
      "enabled": $SPOTLIGHT_INT_RLM,
      "mode": "$SPOTLIGHT_RLM_MODE",
      "model": $([ -n "$SPOTLIGHT_RLM_MODEL" ] && printf '"%s"' "$SPOTLIGHT_RLM_MODEL" || echo null),
      "prefilter": $([ "$SPOTLIGHT_RLM_PREFILTER" = "true" ] && echo true || echo false),
      "hybrid": $([ "$SPOTLIGHT_RLM_HYBRID" = "true" ] && echo true || echo false),
      "evidence_boundary": "lead-only; never verified or publishable"
    }
  },
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "last_used": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
CONFIG_EOF
fi

step "Case workspace and vault scaffold"
run mkdir -p "$SPOTLIGHT_CASES_ROOT" "$SPOTLIGHT_VAULT_PATH/evidence" "$SPOTLIGHT_VAULT_PATH/captures" "$SPOTLIGHT_VAULT_PATH/briefs" "$SPOTLIGHT_VAULT_PATH/exports" "$SPOTLIGHT_VAULT_PATH/_schema"
if [ "$DRY_RUN" != "1" ] && [ ! -f "$SPOTLIGHT_VAULT_PATH/index.md" ]; then
  cat > "$SPOTLIGHT_VAULT_PATH/index.md" <<'INDEX_EOF'
---
type: spotlight-index
tags: [spotlight, index]
---
# Spotlight Vault

- investigations/ — verified case summaries ingested after approval
- entities/ — durable people, organizations, places, and objects
- methodology/ — reusable methods and source notes
- tools/ — durable tool notes and integration lessons
- evidence/ — verified source material and citations
- captures/ — local page/document captures
- briefs/ — case summaries and handoffs
- exports/ — publishable packets and review artifacts
INDEX_EOF
fi
if [ "$DRY_RUN" != "1" ] && [ ! -f "$SPOTLIGHT_CASES_ROOT/_template.md" ]; then
  cat > "$SPOTLIGHT_CASES_ROOT/_template.md" <<'CASE_TEMPLATE_EOF'
---
type: spotlight-case
status: draft
tags: [spotlight, investigation]
---
# Case Template

## Lead

## Evidence

## Open Questions
CASE_TEMPLATE_EOF
fi
if [ "$DRY_RUN" != "1" ] && command -v qmd >/dev/null 2>&1; then
  qmd collection add "$SPOTLIGHT_VAULT_PATH" --name spotlight >/dev/null 2>&1 || true
  qmd update >/dev/null 2>&1 || true
  printf "%s✓%s QMD spotlight collection configured\n" "$_c_green" "$_c_reset"
fi
if [ "$SPOTLIGHT_VAULT_APP" = "obsidian" ] && [ "$OS" = "Darwin" ]; then
  run open -a Obsidian "$SPOTLIGHT_VAULT_PATH" 2>/dev/null || true
fi

step "Spotlight command wrappers"
run mkdir -p "$HOME/.local/bin" "$SPOTLIGHT_DIR/.spotlight/logs"
if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: write spotlight-doctor and spotlight-update to ~/.local/bin\n'
else
  cat > "$HOME/.local/bin/spotlight-doctor" <<DOCTOR_EOF
#!/usr/bin/env bash
set -euo pipefail
export PATH="\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH"
expand_path() {
  local input="\$1"
  if [ "\$input" = "~" ]; then printf "%s\\n" "\$HOME"
  elif [[ "\$input" == "~/"* ]]; then printf "%s/%s\\n" "\$HOME" "\${input#~/}"
  else printf "%s\\n" "\$input"; fi
}
SPOTLIGHT_DIR_DEFAULT_INPUT='$SPOTLIGHT_DIR_INPUT'
SPOTLIGHT_VAULT_INPUT='$SPOTLIGHT_VAULT_INPUT'
SPOTLIGHT_DIR_DEFAULT="\$(expand_path "\$SPOTLIGHT_DIR_DEFAULT_INPUT")"
SPOTLIGHT_DIR="\${SPOTLIGHT_DIR:-\$SPOTLIGHT_DIR_DEFAULT}"
SPOTLIGHT_VAULT_PATH="\${SPOTLIGHT_VAULT_PATH:-\$(expand_path "\$SPOTLIGHT_VAULT_INPUT")}"
SPOTLIGHT_CASES_ROOT="\${SPOTLIGHT_CASES_ROOT:-\$SPOTLIGHT_DIR/cases}"
fail=0
ok() { printf "OK    %s\\n" "\$1"; }
bad() { printf "FAIL  %s\\n" "\$1"; fail=1; }
check_path() { [ -e "\$1" ] && ok "\$2" || bad "\$2 missing: \$1"; }
check_cmd() { command -v "\$1" >/dev/null 2>&1 && ok "\$2" || bad "\$2 missing"; }
check_env_name() { grep -q "^\$1=" "\$SPOTLIGHT_DIR/.env" 2>/dev/null && ok "\$1 configured" || bad "\$1 missing from .env"; }
check_path "\$SPOTLIGHT_DIR/.git" "Spotlight repo"
check_path "\$SPOTLIGHT_DIR/AGENTS.md" "AGENTS runtime contract"
check_path "\$SPOTLIGHT_DIR/.spotlight-config.json" "Spotlight config"
check_path "\$SPOTLIGHT_DIR/.env" "Spotlight env"
check_env_name FIRECRAWL_API_KEY
check_env_name OSINT_NAV_API_KEY
check_path "\$SPOTLIGHT_VAULT_PATH" "Spotlight vault"
check_path "\$SPOTLIGHT_CASES_ROOT" "Spotlight case workspace"
check_cmd firecrawl "Firecrawl CLI"
check_cmd qmd "QMD CLI"
DOCTOR_EOF
  # Append runtime-specific checks
  case "$SPOTLIGHT_RUNTIME" in
    local)
      if [ "$SPOTLIGHT_AGENT" = "pi" ]; then
        echo 'check_cmd pi "pi (local agent)"' >> "$HOME/.local/bin/spotlight-doctor"
      else
        echo 'check_cmd opencode "opencode (local agent)"' >> "$HOME/.local/bin/spotlight-doctor"
      fi
      echo 'check_cmd spotlight-local "Spotlight local launcher"' >> "$HOME/.local/bin/spotlight-doctor"
      ;;
    claude)   echo 'check_cmd claude "Claude Code"' >> "$HOME/.local/bin/spotlight-doctor"; echo 'check_path "$SPOTLIGHT_DIR/CLAUDE.md" "Claude context link"' >> "$HOME/.local/bin/spotlight-doctor" ;;
    gemini)   echo 'check_cmd gemini "Gemini"' >> "$HOME/.local/bin/spotlight-doctor"; echo 'check_path "$SPOTLIGHT_DIR/GEMINI.md" "Gemini context link"' >> "$HOME/.local/bin/spotlight-doctor" ;;
    codex)    echo 'check_cmd codex "Codex"' >> "$HOME/.local/bin/spotlight-doctor" ;;
    opencode) echo 'check_cmd opencode "OpenCode"' >> "$HOME/.local/bin/spotlight-doctor" ;;
  esac
  [ -n "$SPOTLIGHT_CLOUD_KEY_VAR" ] && echo "check_env_name $SPOTLIGHT_CLOUD_KEY_VAR" >> "$HOME/.local/bin/spotlight-doctor"
  [ "$SPOTLIGHT_INT_JUNKIPEDIA" = "true" ] && echo 'check_env_name JUNKIPEDIA_API_KEY' >> "$HOME/.local/bin/spotlight-doctor"
  [ "$SPOTLIGHT_INT_UNPAYWALL" = "true" ] && echo 'check_env_name UNPAYWALL_EMAIL' >> "$HOME/.local/bin/spotlight-doctor"
  [ "$SPOTLIGHT_INT_RLM" = "true" ] && echo 'check_env_name SPOTLIGHT_RLM_MODE' >> "$HOME/.local/bin/spotlight-doctor"
  cat >> "$HOME/.local/bin/spotlight-doctor" <<'DOCTOR_TAIL'
if [ -x "$SPOTLIGHT_DIR/tests/smoke.sh" ]; then (cd "$SPOTLIGHT_DIR" && bash tests/smoke.sh >/dev/null) && ok "Smoke test" || bad "Smoke test failed"; fi
if [ -x "$SPOTLIGHT_DIR/integrations/preflight.py" ] || [ -f "$SPOTLIGHT_DIR/integrations/preflight.py" ]; then (cd "$SPOTLIGHT_DIR" && set -a && . .env && set +a && python3 integrations/preflight.py --text >/dev/null) && ok "Integration preflight" || bad "Integration preflight reported issues"; fi
if [ "$fail" -eq 0 ]; then printf "\nSpotlight doctor: OK\n"; else printf "\nSpotlight doctor: failed\n"; fi
exit "$fail"
DOCTOR_TAIL
  chmod +x "$HOME/.local/bin/spotlight-doctor"

  cat > "$HOME/.local/bin/spotlight-update" <<UPDATE_EOF
#!/usr/bin/env bash
set -euo pipefail
expand_path() {
  local input="\$1"
  if [ "\$input" = "~" ]; then printf "%s\\n" "\$HOME"
  elif [[ "\$input" == "~/"* ]]; then printf "%s/%s\\n" "\$HOME" "\${input#~/}"
  else printf "%s\\n" "\$input"; fi
}
SPOTLIGHT_DIR_DEFAULT_INPUT='$SPOTLIGHT_DIR_INPUT'
SPOTLIGHT_DIR_DEFAULT="\$(expand_path "\$SPOTLIGHT_DIR_DEFAULT_INPUT")"
SPOTLIGHT_DIR="\${SPOTLIGHT_DIR:-\$SPOTLIGHT_DIR_DEFAULT}"
log_dir="\$SPOTLIGHT_DIR/.spotlight/logs"
mkdir -p "\$log_dir"
log="\$log_dir/update.log"
exec >>"\$log" 2>&1
echo ""
date
[ -d "\$SPOTLIGHT_DIR/.git" ] || { echo "Spotlight repo missing: \$SPOTLIGHT_DIR"; exit 1; }
cd "\$SPOTLIGHT_DIR"
if ! git diff --quiet || ! git diff --cached --quiet; then echo "Spotlight has local uncommitted changes; skipping update."; exit 0; fi
before="\$(git rev-parse HEAD)"
git fetch origin main
if git merge-base --is-ancestor HEAD origin/main; then
  git merge --ff-only origin/main
  after="\$(git rev-parse HEAD)"
  echo "Spotlight \$before -> \$after"
else
  echo "Spotlight has local commits or divergent history; skipping update."
  exit 0
fi
if "\$HOME/.local/bin/spotlight-doctor"; then
  echo "doctor passed"
else
  echo "doctor failed after update; rolling back to \$before"
  git reset --hard "\$before"
  exit 1
fi
echo "update complete"
UPDATE_EOF
  chmod +x "$HOME/.local/bin/spotlight-update"
  printf "%s✓%s spotlight doctor/update wrappers installed\n" "$_c_green" "$_c_reset"
fi

# === Install spotlight shell command ===
SHELL_RC=""
case "$SHELL" in
  */zsh) SHELL_RC="$HOME/.zshrc" ;;
  */bash) SHELL_RC="$HOME/.bashrc" ;;
  *) SHELL_RC="$HOME/.profile" ;;
esac

if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: write spotlight() function to %s\n' "$SHELL_RC"
else
  touch "$SHELL_RC"
  tmp_rc="$(mktemp)"
  awk '
    /^# SPOTLIGHT-BEGIN/ { skip=1; next }
    /^# SPOTLIGHT-END/ { skip=0; next }
    !skip { print }
  ' "$SHELL_RC" > "$tmp_rc"
  mv "$tmp_rc" "$SHELL_RC"
  echo "→ Writing \"spotlight\" command to $SHELL_RC"

  # Pick the bin to launch based on runtime + agent
  case "$SPOTLIGHT_RUNTIME" in
    local)
      LAUNCH_BIN="spotlight-local"
      ;;
    claude)   LAUNCH_BIN="claude" ;;
    gemini)   LAUNCH_BIN="gemini" ;;
    codex)    LAUNCH_BIN="codex" ;;
    opencode) LAUNCH_BIN="opencode" ;;
  esac

  {
    echo ""
    echo "# SPOTLIGHT-BEGIN — added by install-spotlight.sh"
    printf "export SPOTLIGHT_DIR=%q\n" "$SPOTLIGHT_DIR"
    cat <<SHELL_EOF
export PATH="\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH"
spotlight() {
  local dir="\${SPOTLIGHT_DIR:-\$HOME/spotlight}"
  if [ ! -d "\$dir" ]; then
    echo "Spotlight not installed at \$dir. Re-run the installer." >&2
    return 1
  fi
  case "\${1:-}" in
    update)
      SPOTLIGHT_DIR="\$dir" "\$HOME/.local/bin/spotlight-update"
      ;;
    doctor)
      SPOTLIGHT_DIR="\$dir" "\$HOME/.local/bin/spotlight-doctor"
      ;;
    --help|-h|help)
      cat <<HELP
Usage: spotlight [subcommand]

  (no arg)   Launch the configured runtime and start investigating
  update     Fetch origin/main, fast-forward only, then run doctor
  doctor     Run the smoke test (checks structure, schemas, preflights)
  help       This message
HELP
      return 0
      ;;
  esac
  (cd "\$dir" && { [ -f .env ] && set -a && source .env && set +a; } && $LAUNCH_BIN)
}
# SPOTLIGHT-END
SHELL_EOF
  } >> "$SHELL_RC"
fi

step "Preflight"
if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: source .env + run python3 integrations/preflight.py --text\n'
else
  set -a; source "$SPOTLIGHT_DIR/.env"; set +a
  python3 "$SPOTLIGHT_DIR/integrations/preflight.py" --text || true
fi

# Personalized post-install guide, written by the configurator.
GETTING_STARTED="$SPOTLIGHT_PROFILE_DIR/getting-started.html"
if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: open %s\n' "$GETTING_STARTED"
elif [ -f "$GETTING_STARTED" ]; then
  if [ "$OS" = "Darwin" ]; then
    open "$GETTING_STARTED" >/dev/null 2>&1 || true
  elif command -v wslview >/dev/null 2>&1; then
    wslview "$GETTING_STARTED" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$GETTING_STARTED" >/dev/null 2>&1 || true
  fi
fi

echo ""
echo "${_c_green}${_c_bold}  ╔════════════════════════════════════════════════╗${_c_reset}"
echo "${_c_green}${_c_bold}  ║   ✓  Spotlight installed                       ║${_c_reset}"
echo "${_c_green}${_c_bold}  ╚════════════════════════════════════════════════╝${_c_reset}"
echo ""
echo "  Open a new terminal window and type:"
echo ""
echo "    ${_c_bold}spotlight${_c_reset}"
echo ""
echo "  Then tell the agent: ${_c_dim}Start a Spotlight investigation on <your lead>${_c_reset}"
echo ""
echo "  Docs: $SPOTLIGHT_DIR/docs/README.md"
echo ""
