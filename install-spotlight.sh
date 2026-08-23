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

PUBLIC_BUNDLE_PHASE=0
if [ "${1:-}" = "--provision-from-public-bundle" ]; then
  PUBLIC_BUNDLE_PHASE=1
  shift
fi

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
CONFIGURATOR_VERSION="3"
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
    echo "→ Existing Spotlight install found at $REUSE_CANDIDATE; the public installer will reconfigure it in place."
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
      curl -fsSL https://spotlight.buriedsignals.com/install/navigator_bridge.py -o "$TMP_ASSETS/install/navigator_bridge.py"
      curl -fsSL https://spotlight.buriedsignals.com/install/navigator-transport-matrix.json -o "$TMP_ASSETS/install/navigator-transport-matrix.json"
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
    python3 "$CONFIGURATOR_DIR/install/setup_server.py" --profile-dir "$SPOTLIGHT_PROFILE_DIR" --repo-dir "$CONFIGURATOR_DIR" --legacy-only
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
  elif [[ "$input" == "~/"* ]]; then printf "%s/%s\n" "$HOME" "${input#\~/}"
  else printf "%s\n" "$input"; fi
}

if [ -n "${SPOTLIGHT_DIR_INPUT:-}" ]; then
  # The configurator/headless request is authoritative. An exported
  # SPOTLIGHT_DIR from an older Mycroft-embedded install must not redirect a
  # newly submitted install to unrelated state (F56).
  SPOTLIGHT_DIR="$(expand_path "$SPOTLIGHT_DIR_INPUT")"
elif [ -n "${SPOTLIGHT_DIR:-}" ]; then
  # Backward-compatible reuse for installs created before setup-config.env
  # retained SPOTLIGHT_DIR_INPUT.
  SPOTLIGHT_DIR="$(expand_path "$SPOTLIGHT_DIR")"
  SPOTLIGHT_DIR_INPUT="$SPOTLIGHT_DIR"
else
  echo "install path missing from config" >&2
  exit 1
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
FIRECRAWL_CLI_VERSION="1.9.8"
OPEN_KNOWLEDGE_VERSION="0.54.3"
DEV_BROWSER_VERSION="0.2.8"
CLAUDE_CODE_VERSION="2.1.169"
OPENAI_CODEX_VERSION="0.138.0"
OPENCODE_AI_VERSION="1.17.7"
PI_CODING_AGENT_VERSION="0.79.6"
JSONSCHEMA_VERSION="4.25.1"
REQUESTS_VERSION="2.32.5"
MAIGRET_VERSION="0.4.4"
CRAWL4AI_VERSION="0.9.0"
SEARXNG_IMAGE="searxng/searxng@sha256:4ebe6ab0297397426fe7ca015caab8f5ed3b50784cf968bfbda7887ea6b16f6e"

# Defaults for optional config fields
: "${SPOTLIGHT_MODE:=cloud}"
: "${SPOTLIGHT_RUNTIME:=claude}"
: "${SPOTLIGHT_LOCAL_SERVER:=}"
: "${SPOTLIGHT_LOCAL_MODEL:=gemma12b}"
# model_tier drives integration dismissal (`12b` = constrained: native dev-browser +
# Crawl4AI seam + osint-tools SQL only; 26b|31b|frontier|api = integrations on), the
# harness compaction profile, and the launcher's reasoning budget. The configurator
# sends it; when unset, derive it from the model choice.
if [ -z "${SPOTLIGHT_MODEL_TIER:-}" ]; then
  case "${SPOTLIGHT_LOCAL_MODEL:-}" in
    gemma12b) SPOTLIGHT_MODEL_TIER="12b" ;;
    gemma26b) SPOTLIGHT_MODEL_TIER="26b" ;;
    gemma31b) SPOTLIGHT_MODEL_TIER="31b" ;;
    *) SPOTLIGHT_MODEL_TIER="$([ "$SPOTLIGHT_MODE" = "local" ] && echo 26b || echo frontier)" ;;
  esac
fi
: "${SPOTLIGHT_AGENT:=opencode}"
: "${SPOTLIGHT_OPENCODE_INTERFACE:=cli}"
: "${SPOTLIGHT_OPENCODE_PROVIDER:=}"
: "${SPOTLIGHT_CLOUD_KEY_VAR:=}"
: "${SPOTLIGHT_CLOUD_KEY:=}"
: "${SPOTLIGHT_MODEL_REPO:=}"
: "${SPOTLIGHT_INT_DEVBROWSER:=true}"
: "${SPOTLIGHT_INT_JUNKIPEDIA:=false}"
: "${JUNKIPEDIA_API_KEY:=}"
: "${SPOTLIGHT_INT_UNPAYWALL:=false}"
: "${UNPAYWALL_EMAIL:=}"
# RLM is runtime-auto on the local tier (PRD): `fetch` distills every scraped page
# through the local e4b AND the harness uses it as the conversation-compaction
# summarizer. Cloud/frontier keep it opt-in (distillation is a skill-gated proposal there).
: "${SPOTLIGHT_INT_RLM:=$([ "$SPOTLIGHT_MODE" = "local" ] && echo true || echo false)}"
: "${SPOTLIGHT_RLM_MODE:=$([ "$SPOTLIGHT_MODE" = "local" ] && echo local_llamacpp_e4b || echo off)}"
: "${SPOTLIGHT_RLM_MODEL:=}"
: "${SPOTLIGHT_RLM_PREFILTER:=}"
: "${SPOTLIGHT_RLM_HYBRID:=}"
# The RLM GGUF source (HF repo + file). Defaults to the stock instruction-tuned e4b
# (verified public on HF). Empty = no download; the launcher serves the RLM only when
# SPOTLIGHT_RLM_GGUF_PATH (written to .env) exists, and degrades gracefully (raw
# fetches, session-model compaction) when it doesn't.
: "${SPOTLIGHT_RLM_REPO:=unsloth/gemma-4-E4B-it-GGUF}"
: "${SPOTLIGHT_RLM_GGUF:=gemma-4-E4B-it-Q4_K_M.gguf}"
: "${FIRECRAWL_API_KEY:=}"
# Navigator is optional for the public install. The unified skill is present
# even in locked mode; the direct login flow stores its PAT in Navigator's own
# keyring and never stages it in Spotlight's environment.
: "${OSINT_NAV_API_KEY:=}"
: "${SPOTLIGHT_NAVIGATOR_CONNECTION:=locked}"

if [ "$SPOTLIGHT_INT_RLM" != "true" ]; then
  SPOTLIGHT_RLM_MODE="off"
  SPOTLIGHT_RLM_MODEL=""
  SPOTLIGHT_RLM_PREFILTER=""
  SPOTLIGHT_RLM_HYBRID=""
fi

# Derive model artifact names from the model selection.
#
# Roster (2026-07-09): the Gemma-4 sovereign tiers, all llama.cpp GGUFs (repo +
# filename pairs verified against the HF API; the configurator sends the repo via
# SPOTLIGHT_MODEL_REPO — see install/setup_server.py MODEL_REPOS):
#
#   gemma12b — Spotlight procedure-tuned orchestrator (v5, Q4_K_M,
#              ~7 GB). Default; the speed pick; 16 GB min, 24 GB for the full
#              stack with the RLM. Trained on real Spotlight runs to drive the
#              gated pipeline (see going-sovereign.html).
#   gemma26b — unsloth gemma-4-26B-A4B MoE (UD-Q4_K_M, ~18 GB). 26B knowledge,
#              4B active per token — near-12B decode speed. 32 GB min.
#   gemma31b — unsloth gemma-4-31B (Q4_K_M, ~18 GB). Strongest local tier on the
#              OSINT benchmark (facet 0.881 at Q4, lowest hallucination). Dense —
#              slower prompt-processing on deep investigations. 48 GB for the
#              full stack.
#
# Removed earlier for ethics failures (doorstep/children probe): qwen9b + the
# abliterated e4b journalist (HF repos deleted). qwen27b retired with the Ollama
# path (its no-think codepath is damaged; needs Ollama stop-token workarounds).
case "$SPOTLIGHT_LOCAL_MODEL" in
  gemma12b) GGUF_FILE="gemma-4-12b-spotlight-orchestrator-Q4_K_M.gguf" ;;
  gemma26b) GGUF_FILE="gemma-4-26B-A4B-it-UD-Q4_K_M.gguf" ;;
  gemma31b) GGUF_FILE="gemma-4-31B-it-Q4_K_M.gguf" ;;
  *)        GGUF_FILE="" ;;
esac

# Local serving is llama.cpp, full stop: the Flue/Pi harness needs llama-server's
# --jinja tool-calling grammar, which Ollama cannot expose for these models (verified
# U6: "does not support tools"). An Ollama setup choice is coerced with a notice.
if [ "$SPOTLIGHT_MODE" = "local" ] && [ "$SPOTLIGHT_LOCAL_SERVER" != "llamacpp" ]; then
  printf '→ Local serving runs on llama.cpp (the harness needs --jinja tool-calling; Ollama cannot serve tools for these models). Overriding SPOTLIGHT_LOCAL_SERVER=%s.\n' "${SPOTLIGHT_LOCAL_SERVER:-<unset>}"
  SPOTLIGHT_LOCAL_SERVER="llamacpp"
fi
if [ "$SPOTLIGHT_LOCAL_SERVER" = "llamacpp" ]; then
  LOCAL_PORT="8080"
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

if [ "$PUBLIC_BUNDLE_PHASE" = "0" ] && [ "$DRY_RUN" = "0" ]; then
  PUBLIC_RELEASE_BASE="https://github.com/buriedsignals/spotlight/releases/download/v2.1.9"
  PUBLIC_BOOTSTRAP_SHA256="cfc35920ac7ad8d5615c7db12285103d69666b39d4ef863f691c62a4d89fc0f8"
  command -v curl >/dev/null 2>&1 || { echo "Spotlight installer: curl is required" >&2; exit 1; }
  command -v openssl >/dev/null 2>&1 || { echo "Spotlight installer: OpenSSL is required" >&2; exit 1; }
  PUBLIC_BOOTSTRAP_TMP="$(mktemp "${TMPDIR:-/tmp}/spotlight-public-bootstrap.XXXXXX")"
  trap 'rm -f "$PUBLIC_BOOTSTRAP_TMP"' EXIT HUP INT TERM
  curl -fL --proto '=https' --tlsv1.2 "$PUBLIC_RELEASE_BASE/bootstrap.sh" -o "$PUBLIC_BOOTSTRAP_TMP"
  PUBLIC_BOOTSTRAP_ACTUAL="$(openssl dgst -sha256 -r "$PUBLIC_BOOTSTRAP_TMP" | awk '{print $1}')"
  [ "$PUBLIC_BOOTSTRAP_ACTUAL" = "$PUBLIC_BOOTSTRAP_SHA256" ] || {
    echo "Spotlight installer: public bootstrap digest did not verify" >&2
    exit 1
  }
  bash "$PUBLIC_BOOTSTRAP_TMP" --product spotlight --release-base "$PUBLIC_RELEASE_BASE" \
    --runtime "$SPOTLIGHT_RUNTIME" --product-root "$SPOTLIGHT_DIR"
  exit $?
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
# namespace. skills.manifest (generated by the Engine release export) lists exactly
# the set; fall back to the on-disk skill dirs if it is absent.
SPOTLIGHT_SKILLS_ROOT="$HOME/.agents/skills"
if [ -L "$SPOTLIGHT_SKILLS_ROOT" ]; then
  # A symlinked root is user-managed. Never mkdir or place links through it:
  # that would mutate the target repository (and a dangling child makes mkdir
  # fail outright). Runtime adapters point at this private fallback instead.
  SPOTLIGHT_CANONICAL_SKILLS="$HOME/.local/share/spotlight-skills/spotlight"
  printf '→ ~/.agents/skills is user-managed; using private Spotlight skills store: %s\n' "$SPOTLIGHT_CANONICAL_SKILLS"
else
  SPOTLIGHT_CANONICAL_SKILLS="$SPOTLIGHT_SKILLS_ROOT/spotlight"
fi

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

# Flue discovers flat skill names from <cwd>/.agents/skills. The public local
# runtime uses the OpenKnowledge project as its cwd, so project only the
# manifest-owned Spotlight names there. Never replace user-owned workspace
# content or a foreign symlink.
project_spotlight_skills_into_workspace() {
  local _dest="$SPOTLIGHT_VAULT_PATH/.agents/skills" _sid _link _target
  run mkdir -p "$_dest"
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: project manifest skills into OpenKnowledge workspace %s\n' "$_dest"
    return 0
  fi
  while IFS= read -r _sid; do
    { [ -n "$_sid" ] && [ -e "$SPOTLIGHT_CANONICAL_SKILLS/$_sid" ]; } || continue
    _link="$_dest/$_sid"
    _target="$SPOTLIGHT_CANONICAL_SKILLS/$_sid"
    if [ -L "$_link" ]; then
      if [ "$(readlink "$_link")" != "$_target" ]; then
        printf "%s!%s Leaving foreign workspace skill link unchanged: %s\n" "$_c_yellow" "$_c_reset" "$_link"
      fi
      continue
    fi
    if [ -e "$_link" ]; then
      printf "%s!%s Leaving user-owned workspace skill unchanged: %s\n" "$_c_yellow" "$_c_reset" "$_link"
      continue
    fi
    ln -s "$_target" "$_link"
  done < "$SPOTLIGHT_DIR/skills.manifest"
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
  if [ "$OS" != "Darwin" ]; then
    echo "Homebrew is not used on Linux. Install the missing package with your system package manager, then re-run Spotlight." >&2
    return 1
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
  if [ "$OS" = "Linux" ]; then
    echo "$cmd is required. Install it with your Linux package manager, then re-run Spotlight." >&2
    return 1
  fi
  ensure_brew
  spin "Installing $cmd via brew" brew install "$pkg"
}

ensure_python_runtime() {
  local candidate
  for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
      SPOTLIGHT_BOOTSTRAP_PYTHON="$(command -v "$candidate")"
      printf "%s✓%s Python 3.11+ present (%s)\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_BOOTSTRAP_PYTHON"
      return 0
    fi
  done
  if [ "$OS" = "Linux" ]; then
    echo "Spotlight requires Python 3.11+ for Crawl4AI and Navigator. Install python3.11+ plus its venv package, then re-run." >&2
    return 1
  fi
  ensure_brew
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: install Python 3.12 via Homebrew\n'
    SPOTLIGHT_BOOTSTRAP_PYTHON=python3.12
    return 0
  fi
  spin "Installing Python 3.12 via brew" brew install python@3.12
  SPOTLIGHT_BOOTSTRAP_PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
  [ -x "$SPOTLIGHT_BOOTSTRAP_PYTHON" ] || {
    echo "Homebrew installed python@3.12 but its interpreter was not found." >&2
    return 1
  }
}

spotlight_writable_prefix() {
  local path="$1" parent
  while [ ! -e "$path" ]; do
    parent="$(dirname "$path")"
    [ "$parent" = "$path" ] && break
    path="$parent"
  done
  [ -d "$path" ] && [ -w "$path" ]
}

prepare_npm_prefix() {
  local prefix user_prefix
  command -v npm >/dev/null 2>&1 || {
    echo "npm is required. Install Node.js 20+ and re-run Spotlight." >&2
    return 1
  }
  prefix="$(npm config get prefix 2>/dev/null)" || {
    echo "Could not read npm's global prefix. Repair npm, then re-run Spotlight." >&2
    return 1
  }
  [ -n "$prefix" ] || {
    echo "npm returned an empty global prefix. Repair npm, then re-run Spotlight." >&2
    return 1
  }
  if ! spotlight_writable_prefix "$prefix"; then
    user_prefix="$HOME/.npm-global"
    run mkdir -p "$user_prefix/bin"
    if [ "$DRY_RUN" = "1" ]; then
      printf 'DRY-RUN: npm config set prefix %s\n' "$user_prefix"
    else
      npm config set prefix "$user_prefix"
    fi
    prefix="$user_prefix"
    printf 'Using user-writable npm prefix: %s\n' "$prefix"
  fi
  case ":$PATH:" in
    *":$prefix/bin:"*) ;;
    *) export PATH="$prefix/bin:$PATH" ;;
  esac
}

preflight_linux_floor() {
  [ "$OS" = "Linux" ] || return 0
  local tool missing=""
  for tool in curl git node npm python3 make cc c++; do
    command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
  done
  if [ -n "$missing" ]; then
    printf 'Missing Linux prerequisites:%s\n' "$missing" >&2
    printf 'Install the Spotlight dependency floor, then re-run:\n' >&2
    printf '  sudo apt-get update && sudo apt-get install -y curl git nodejs npm python3 python3-venv build-essential\n' >&2
    return 1
  fi
}

reviewed_npm_version() {
  case "$1" in
    firecrawl-cli) echo "$FIRECRAWL_CLI_VERSION" ;;
    @inkeep/open-knowledge) echo "$OPEN_KNOWLEDGE_VERSION" ;;
    dev-browser) echo "$DEV_BROWSER_VERSION" ;;
    @anthropic-ai/claude-code) echo "$CLAUDE_CODE_VERSION" ;;
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
  SPOTLIGHT_PYTHON_ENV="$SPOTLIGHT_DIR/.venv"
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: %s -m venv %s\n' "$SPOTLIGHT_BOOTSTRAP_PYTHON" "$SPOTLIGHT_PYTHON_ENV"
    printf 'DRY-RUN: %s/bin/python -m pip install --quiet jsonschema==%s requests==%s crawl4ai==%s\n' \
      "$SPOTLIGHT_PYTHON_ENV" "$JSONSCHEMA_VERSION" "$REQUESTS_VERSION" "$CRAWL4AI_VERSION"
    printf 'DRY-RUN: %s/bin/crawl4ai-setup\n' "$SPOTLIGHT_PYTHON_ENV"
    return 0
  fi
  if [ ! -x "$SPOTLIGHT_PYTHON_ENV/bin/python" ]; then
    if ! "$SPOTLIGHT_BOOTSTRAP_PYTHON" -m venv "$SPOTLIGHT_PYTHON_ENV"; then
      echo "Could not create $SPOTLIGHT_PYTHON_ENV. On Debian/Ubuntu install python3-venv, then re-run Spotlight." >&2
      return 1
    fi
  fi
  spin "Installing reviewed Python runtime" "$SPOTLIGHT_PYTHON_ENV/bin/python" -m pip install --quiet \
    "jsonschema==$JSONSCHEMA_VERSION" \
    "requests==$REQUESTS_VERSION" \
    "crawl4ai==$CRAWL4AI_VERSION"
  spin "Installing Crawl4AI browser runtime" "$SPOTLIGHT_PYTHON_ENV/bin/crawl4ai-setup"
  "$SPOTLIGHT_PYTHON_ENV/bin/python" - "$JSONSCHEMA_VERSION" "$REQUESTS_VERSION" "$CRAWL4AI_VERSION" <<'PY'
import importlib.metadata as metadata
import sys

expected = {
    "jsonschema": sys.argv[1],
    "requests": sys.argv[2],
    "crawl4ai": sys.argv[3],
}
for package, version in expected.items():
    actual = metadata.version(package)
    if actual != version:
        raise SystemExit(f"{package} version mismatch: expected {version}, found {actual}")
PY
  SPOTLIGHT_PYTHON="$SPOTLIGHT_PYTHON_ENV/bin/python"
  export SPOTLIGHT_PYTHON
  export PATH="$SPOTLIGHT_PYTHON_ENV/bin:$PATH"
}

searxng_health() {
  curl -fsS "${SEARXNG_URL%/}/search?q=spotlight-health&format=json" 2>/dev/null \
    | python3 -c 'import json,sys; data=json.load(sys.stdin); assert isinstance(data.get("results"), list)' >/dev/null 2>&1
}

provision_searxng() {
  : "${SEARXNG_URL:=http://127.0.0.1:8899}"
  local container="spotlight-searxng" settings="$HOME/.config/spotlight/searxng/settings.yml"
  local inspect="" owner="" image="" running=""

  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: reuse healthy SearXNG at %s or run %s as %s on loopback port 8899\n' \
      "$SEARXNG_URL" "$SEARXNG_IMAGE" "$container"
    return 0
  fi
  if searxng_health; then
    printf "%s✓%s SearXNG healthy at %s (reusing existing service)\n" "$_c_green" "$_c_reset" "$SEARXNG_URL"
    return 0
  fi
  command -v docker >/dev/null 2>&1 || {
    echo "Docker is required for sovereign SearXNG search. Install/start Docker, then re-run Spotlight (or configure a healthy SEARXNG_URL)." >&2
    return 1
  }
  docker info >/dev/null 2>&1 || {
    echo "Docker is installed but its daemon is unavailable. Start Docker, then re-run Spotlight." >&2
    return 1
  }

  inspect="$(docker inspect --format '{{index .Config.Labels "com.buriedsignals.product"}}|{{.Config.Image}}|{{.State.Running}}' "$container" 2>/dev/null || true)"
  if [ -n "$inspect" ]; then
    IFS='|' read -r owner image running <<<"$inspect"
    if [ "$owner" != "spotlight" ]; then
      echo "Docker container $container exists but is not owned by Spotlight; refusing to replace it." >&2
      return 1
    fi
    if [ "$image" != "$SEARXNG_IMAGE" ]; then
      docker rm -f "$container" >/dev/null
      inspect=""
    elif [ "$running" != "true" ]; then
      docker start "$container" >/dev/null
    fi
  fi

  if [ -z "$inspect" ]; then
    mkdir -p "$(dirname "$settings")"
    if [ ! -e "$settings" ]; then
      local secret old_umask
      secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
      old_umask="$(umask)"
      umask 077
      printf 'use_default_settings: true\nserver:\n  secret_key: "%s"\n  limiter: false\n  image_proxy: false\nsearch:\n  formats:\n    - html\n    - json\n' "$secret" > "$settings"
      umask "$old_umask"
    elif [ -L "$settings" ] || [ ! -f "$settings" ]; then
      echo "Refusing unsafe SearXNG settings path: $settings" >&2
      return 1
    fi
    docker run -d --name "$container" \
      --label com.buriedsignals.product=spotlight \
      --restart unless-stopped --network bridge --cap-drop ALL \
      --security-opt no-new-privileges --memory 1g --cpus 1.0 \
      -p 127.0.0.1:8899:8080 \
      -v "$settings:/etc/searxng/settings.yml:ro" \
      "$SEARXNG_IMAGE" >/dev/null
  fi

  local attempt
  for attempt in $(seq 1 60); do
    searxng_health && {
      printf "%s✓%s SearXNG ready at %s\n" "$_c_green" "$_c_reset" "$SEARXNG_URL"
      return 0
    }
    sleep 0.5
  done
  echo "SearXNG did not become ready at $SEARXNG_URL; inspect with: docker logs $container" >&2
  return 1
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

preflight_linux_floor

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
ensure_python_runtime
prepare_npm_prefix
if [ "$SPOTLIGHT_MODE" = "local" ]; then
  if [ "$OS" = "Darwin" ]; then
    ensure_brew  # macOS local mode uses brew for the inference server + agent
  elif [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: Linux local mode will use the system llama.cpp package; Homebrew is not required\n'
  fi
fi

step "Spotlight repo"
if [ "$PUBLIC_BUNDLE_PHASE" = "1" ]; then
  [ -d "$SPOTLIGHT_DIR/.git" ] || { echo "Spotlight installer: the signed public bundle did not create the product checkout" >&2; exit 1; }
elif [ -d "$SPOTLIGHT_DIR/.git" ]; then
  spin "Updating Spotlight at $SPOTLIGHT_DIR" update_repo_ff_only "$SPOTLIGHT_DIR" "Spotlight"
else
  spin "Cloning Spotlight to $SPOTLIGHT_DIR" git clone "$REPO_URL" "$SPOTLIGHT_DIR"
fi
cd "$SPOTLIGHT_DIR"

step "Core dependencies"
if [ -n "$FIRECRAWL_API_KEY" ]; then
  ensure_npm_global_exact firecrawl firecrawl-cli
else
  printf "%s→%s Firecrawl not configured; using sovereign SearXNG + Crawl4AI only\n" "$_c_yellow" "$_c_reset"
fi
ensure_npm_global_exact open-knowledge @inkeep/open-knowledge

# =====================================================================
# LOCAL MODE — inference server + agent harness
# =====================================================================
if [ "$SPOTLIGHT_MODE" = "local" ]; then

  # ---- Inference server: llama.cpp (the launcher serves orchestrator + RLM) ----
    step "Local inference (llama-server)"
    if [ -z "$MODEL_LEAF" ] || [ -z "$GGUF_FILE" ]; then
      echo "No GGUF model selected (SPOTLIGHT_MODEL_REPO / SPOTLIGHT_LOCAL_MODEL). Re-run setup.html and pick a llama.cpp model." >&2
      exit 1
    fi
    if ! command -v llama-server >/dev/null 2>&1; then
      if [ "$DRY_RUN" = "1" ] && [ "$OS" = "Linux" ]; then
        # CI and users can inspect a Linux dry-run without having llama.cpp
        # preinstalled. A real Linux install still fails in ensure_tool-style
        # fashion with an actionable package-manager message below.
        printf 'DRY-RUN: install llama.cpp via the Linux package manager (llama-server)\n'
      elif [ "$OS" = "Linux" ]; then
        echo "llama-server is required for local mode. Install llama.cpp with your Linux package manager, then re-run Spotlight." >&2
        exit 1
      else
        spin "Installing llama.cpp via brew" brew install llama.cpp
      fi
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
  # ---- RLM model (context hygiene: fetch distillation + compaction summarizer) ----
  # Runtime-auto on the local tier: served by the launcher on its own llama.cpp so
  # `fetch --rlm` distills every scraped page (~99% token saving) AND the harness
  # summarizes conversation compactions cheaply instead of blocking the session model.
  SPOTLIGHT_RLM_GGUF_PATH=""
  if [ "$SPOTLIGHT_INT_RLM" = "true" ] && [ -n "$SPOTLIGHT_RLM_REPO" ] && [ -n "$SPOTLIGHT_RLM_GGUF" ]; then
    step "RLM model (context hygiene)"
    RLM_DIR="$HOME/Models/${SPOTLIGHT_RLM_REPO##*/}"
    run mkdir -p "$RLM_DIR"
    if [ ! -f "$RLM_DIR/$SPOTLIGHT_RLM_GGUF" ]; then
      spin "Downloading $SPOTLIGHT_RLM_GGUF from huggingface.co/$SPOTLIGHT_RLM_REPO" \
        curl -L --fail --retry 3 --continue-at - \
          "https://huggingface.co/$SPOTLIGHT_RLM_REPO/resolve/main/$SPOTLIGHT_RLM_GGUF" \
          -o "$RLM_DIR/$SPOTLIGHT_RLM_GGUF.part"
      run mv "$RLM_DIR/$SPOTLIGHT_RLM_GGUF.part" "$RLM_DIR/$SPOTLIGHT_RLM_GGUF"
    else
      printf "%s✓%s RLM model already downloaded at %s\n" "$_c_green" "$_c_reset" "$RLM_DIR/$SPOTLIGHT_RLM_GGUF"
    fi
    SPOTLIGHT_RLM_GGUF_PATH="$RLM_DIR/$SPOTLIGHT_RLM_GGUF"
  elif [ "$SPOTLIGHT_INT_RLM" = "true" ]; then
    printf "%s→%s RLM enabled but no SPOTLIGHT_RLM_REPO/SPOTLIGHT_RLM_GGUF given; set SPOTLIGHT_RLM_GGUF_PATH in %s/.env to serve an on-disk GGUF. Without it the harness degrades gracefully (raw fetches, session-model compaction).\n" "$_c_yellow" "$_c_reset" "$SPOTLIGHT_DIR"
  fi

  # ---- Agent harness: Flue on Pi (ONE harness — the repo's harness/flue) ----
  # The installed harness IS the checkout's harness/flue (source-of-truth invariant:
  # local eval == user experience; no parallel harness). Flue runs on Pi and gives the
  # orchestrator native subagents (investigator / fact-checker in their own child
  # sessions), workspace-discovered skills, and conversation compaction. The launcher
  # below starts llama.cpp serving and runs `flue run spotlight` against it.
  step "Agent harness (Flue on Pi)"
  NODE_MAJOR="$(node -v 2>/dev/null | sed 's/^v//' | cut -d. -f1 || echo 0)"
  if [ "${NODE_MAJOR:-0}" -lt 22 ]; then
    spin "Installing Node 22+ via brew (Flue needs ≥22.19)" brew install node
  fi
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: npm install in %s/harness/flue + link .agents/skills\n' "$SPOTLIGHT_DIR"
  else
    ( cd "$SPOTLIGHT_DIR/harness/flue" && spin "Installing Flue harness deps" npm install --no-audit --no-fund )
    # Flue discovers Agent Skills from <cwd>/.agents/skills at context init. Link
    # ONLY the manifest-listed skills — never the whole skills/ tree — so the
    # catalog/manifest is the actual discovery boundary, not just documentation.
    # (A skill absent from skills.manifest is invisible to Flue, which is what
    # makes tier-/role-filtered manifests enforceable later.)
    run mkdir -p "$SPOTLIGHT_DIR/.agents"
    if [ -L "$SPOTLIGHT_DIR/.agents/skills" ]; then run rm "$SPOTLIGHT_DIR/.agents/skills"; fi
    run mkdir -p "$SPOTLIGHT_DIR/.agents/skills"
    while IFS= read -r _sid; do
      { [ -n "$_sid" ] && [ -d "$SPOTLIGHT_DIR/skills/$_sid" ]; } || continue
      run ln -sfn "$SPOTLIGHT_DIR/skills/$_sid" "$SPOTLIGHT_DIR/.agents/skills/$_sid"
    done < "$SPOTLIGHT_DIR/skills.manifest"
    # Prune managed links that fell out of the manifest (user-owned files untouched).
    for _lnk in "$SPOTLIGHT_DIR/.agents/skills"/*; do
      [ -L "$_lnk" ] || continue
      grep -qx "$(basename "$_lnk")" "$SPOTLIGHT_DIR/skills.manifest" || run rm "$_lnk"
    done
    printf "%s✓%s Flue harness ready (%s/harness/flue; skills via .agents/skills, manifest-scoped)\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_DIR"
  fi
  # Keep the cross-runtime canonical store contract satisfied too.
  place_spotlight_skills_canonical

  # ---- Local launcher script ----
  # SCOPE: the efficiency flags in this launcher (--cache-type-k/v q8_0, --flash-attn,
  # --no-cache-idle-slots --parallel 2) apply ONLY to the constrained LOCAL-serving tier
  # (local GGUF via llama.cpp on consumer devices). API/frontier deployments run in
  # SPOTLIGHT_MODE=cloud with a provider config and NO local llama-server, so they are
  # unaffected and serve normally (provider-managed KV, full precision).
  step "Spotlight local launcher"
  run mkdir -p "$HOME/.local/bin"
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: write ~/.local/bin/spotlight-local (llama.cpp serving + flue run spotlight)\n'
  else
    cat > "$HOME/.local/bin/spotlight-local" <<LAUNCHER_EOF
#!/usr/bin/env bash
# Managed by the Spotlight public installer.
# Spotlight local launcher — llama.cpp serving (orchestrator + RLM) + the Flue/Pi harness.
# ONE harness: this runs the same \`flue run spotlight\` app the evals exercise
# (harness/flue in the Spotlight checkout) — local test == user experience.
#
# Usage:
#   spotlight-local <session-id> "<message>"   start or resume a session; answer each
#                                              gate by re-running with the SAME id
#   spotlight-local --raw [flue run args...]   pass through to \`flue run spotlight\`
#   spotlight-local --stop                     stop the llama.cpp servers
#
# Servers stay resident between invocations (llama-server prefix-caches the
# conversation, so gate replies re-prefill fast); --stop tears them down.
set -euo pipefail
expand_path() {
  local input="\$1"
  if [ "\$input" = "~" ]; then printf "%s\\n" "\$HOME"
  elif [[ "\$input" == "~/"* ]]; then printf "%s/%s\\n" "\$HOME" "\${input#\~/}"
  else printf "%s\\n" "\$input"; fi
}
SPOTLIGHT_DIR_DEFAULT_INPUT='$SPOTLIGHT_DIR_INPUT'
SPOTLIGHT_DIR_DEFAULT="\$(expand_path "\$SPOTLIGHT_DIR_DEFAULT_INPUT")"
SPOTLIGHT_DIR="\${SPOTLIGHT_DIR:-\$SPOTLIGHT_DIR_DEFAULT}"
ENV_FILE="\$SPOTLIGHT_DIR/.env"
if [ -f "\$ENV_FILE" ]; then set -a; . "\$ENV_FILE"; set +a; fi

if [ "\${1:-}" = "--stop" ]; then
  for p in 8080 8095; do lsof -ti:"\$p" | xargs kill -TERM 2>/dev/null || true; done
  echo "Spotlight llama.cpp servers stopped."
  exit 0
fi

# Model + tier come from .env at RUNTIME (switching 12b↔26b↔31b = edit .env, no
# reinstall): SPOTLIGHT_GGUF_PATH overrides the install-time default; the tier picks
# the reasoning budget (bigger models get a longer thinking leash) and the harness's
# compaction profile. SPOTLIGHT_REASONING_BUDGET overrides the tier default.
MODEL="\${SPOTLIGHT_GGUF_PATH:-\$HOME/Models/$MODEL_LEAF/$GGUF_FILE}"
TIER="\${SPOTLIGHT_MODEL_TIER:-12b}"
case "\$TIER" in
  26b) RB_DEFAULT=800 ;;
  31b) RB_DEFAULT=1024 ;;
  *)   RB_DEFAULT=400 ;;
esac
RB="\${SPOTLIGHT_REASONING_BUDGET:-\$RB_DEFAULT}"
FLUE="\$SPOTLIGHT_DIR/harness/flue/node_modules/.bin/flue"
SPOTLIGHT_PYTHON="\${SPOTLIGHT_PYTHON:-\$SPOTLIGHT_DIR/.venv/bin/python}"
MODEL_BASENAME="\$(basename "\$MODEL")"
MODEL_ALIAS="\$(printf 'spotlight-%s-%s' "\$TIER" "\$MODEL_BASENAME" | tr -c 'A-Za-z0-9._-' '-')"
VERIFY_MODEL="\$SPOTLIGHT_DIR/scripts/verify-openai-model.py"
command -v llama-server >/dev/null 2>&1 || { echo "llama-server missing — brew install llama.cpp" >&2; exit 1; }
command -v open-knowledge >/dev/null 2>&1 || { echo "OpenKnowledge CLI missing — re-run install-spotlight.sh" >&2; exit 1; }
[ -x "\$FLUE" ] || { echo "Flue harness missing — re-run install-spotlight.sh (npm install in harness/flue)" >&2; exit 1; }
[ -f "\$MODEL" ] || { echo "Model not found: \$MODEL (set SPOTLIGHT_GGUF_PATH in \$ENV_FILE)" >&2; exit 1; }
[ -x "\$SPOTLIGHT_PYTHON" ] || { echo "Spotlight Python environment missing — re-run install-spotlight.sh" >&2; exit 1; }
[ -f "\$VERIFY_MODEL" ] || { echo "Model identity verifier missing: \$VERIFY_MODEL" >&2; exit 1; }

# Orchestrator model: TWO resident slots (orchestrator + the active subagent), q8_0 KV
# + flash-attn (≈½ KV memory), NO idle-slot save/restore (the delegation
# restore-failure), reasoning budget capped per tier (gemma-4 unbounded thinking
# spends the whole budget and returns EMPTY content). Per-slot context = 81920/2 =
# 40960 — SPOTLIGHT_LOCAL_CTX below MUST match it.
if ! lsof -ti:8080 >/dev/null 2>&1; then
  llama-server --model "\$MODEL" --alias "\$MODEL_ALIAS" --host 127.0.0.1 --port 8080 \\
    --ctx-size 81920 --parallel 2 --no-cache-idle-slots --n-gpu-layers 999 --jinja \\
    --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 --reasoning-budget "\$RB" \\
    >/tmp/llama-server-spotlight.log 2>&1 &
else
  if ! "\$SPOTLIGHT_PYTHON" "\$VERIFY_MODEL" http://127.0.0.1:8080 "\$MODEL_ALIAS"; then
    echo "Port 8080 is occupied by a different service or model; expected \$MODEL_ALIAS. Stop that service or run 'spotlight-local --stop', then retry." >&2
    exit 2
  fi
  echo "Reusing verified llama-server model \$MODEL_ALIAS on :8080"
fi

# RLM (context hygiene): the small distiller on its OWN llama.cpp — \`fetch\`
# distillation (~99% token saving on scraped pages) AND the conversation-compaction
# summarizer. --reasoning-budget 0: a distiller/summarizer must answer, not think.
# Optional: without an RLM GGUF the harness degrades gracefully (raw fetches,
# session-model compaction).
if [ -n "\${SPOTLIGHT_RLM_GGUF_PATH:-}" ] && [ -f "\${SPOTLIGHT_RLM_GGUF_PATH:-}" ]; then
  if ! lsof -ti:8095 >/dev/null 2>&1; then
    llama-server --model "\$SPOTLIGHT_RLM_GGUF_PATH" --alias rlm-e4b --host 127.0.0.1 --port 8095 \\
      --ctx-size 24576 --n-gpu-layers 999 --jinja --flash-attn on \\
      --cache-type-k q8_0 --cache-type-v q8_0 --reasoning-budget 0 \\
      >/tmp/llama-server-rlm.log 2>&1 &
  fi
  export SPOTLIGHT_RLM_OPENAI_BASE_URL="http://127.0.0.1:8095/v1"
  export SPOTLIGHT_RLM_OPENAI_MODEL="rlm-e4b"
  export SPOTLIGHT_RLM_CTX=24576
else
  echo "No RLM GGUF (SPOTLIGHT_RLM_GGUF_PATH) — running without fetch distillation / cheap compaction"
fi

echo -n "Waiting for llama-server"
READY=0
for i in {1..180}; do "\$SPOTLIGHT_PYTHON" "\$VERIFY_MODEL" http://127.0.0.1:8080 "\$MODEL_ALIAS" >/dev/null 2>&1 && { echo " ready."; READY=1; break; }; echo -n "."; sleep 1; done
[ "\$READY" = "1" ] || { echo " llama-server did not become ready after 180s — see /tmp/llama-server-spotlight.log" >&2; exit 1; }
if [ -n "\${SPOTLIGHT_RLM_OPENAI_BASE_URL:-}" ]; then
  for i in {1..60}; do curl -sf http://127.0.0.1:8095/v1/models >/dev/null && break; sleep 1; done
fi

export SPOTLIGHT_LOCAL_BASEURL="http://127.0.0.1:8080/v1"
export SPOTLIGHT_LOCAL_CTX=40960
export SPOTLIGHT_FLUE_MODEL="local/\$MODEL_ALIAS"
export SPOTLIGHT_MODEL_TIER="\$TIER"
# Bind Flue's skill discovery and knowledge tools to the selected OpenKnowledge
# project. The checkout fallback exists only for pre-migration .env files.
export SPOTLIGHT_CWD="\${SPOTLIGHT_WORKSPACE_PATH:-\${SPOTLIGHT_VAULT_PATH:-\$SPOTLIGHT_DIR}}"
export FLUE_DB="\${FLUE_DB:-\$SPOTLIGHT_DIR/harness/flue/data/flue.db}"

# Flue consumes OpenKnowledge's project-scoped Streamable HTTP MCP endpoint.
# Start or reuse the loopback-only project server and publish its resolved port
# before Flue loads the agent module.
open-knowledge --cwd "\$SPOTLIGHT_CWD" config validate >/dev/null
open-knowledge --cwd "\$SPOTLIGHT_CWD" start --only server --no-open-browser --idle-shutdown 30m \
  >>"\$SPOTLIGHT_DIR/.spotlight/logs/openknowledge.log" 2>&1 &
OK_PORT=""
for _ok_try in {1..100}; do
  OK_STATUS="\$(open-knowledge --cwd "\$SPOTLIGHT_CWD" status --json 2>/dev/null || true)"
  OK_PORT="\$(printf '%s' "\$OK_STATUS" | "\$SPOTLIGHT_PYTHON" -c 'import json,sys; d=json.load(sys.stdin); s=d.get("server", {}); print(s.get("port", "") if s.get("alive") is True else "")' 2>/dev/null || true)"
  [ -n "\$OK_PORT" ] && break
  sleep 0.1
done
[ -n "\$OK_PORT" ] || { echo "OpenKnowledge project server did not become ready — see \$SPOTLIGHT_DIR/.spotlight/logs/openknowledge.log" >&2; exit 1; }
export SPOTLIGHT_OPENKNOWLEDGE_MCP_URL="http://127.0.0.1:\$OK_PORT/mcp"

cd "\$SPOTLIGHT_DIR/harness/flue"
if [ "\$#" -eq 0 ]; then
  cat <<USAGE
Spotlight local harness is up (tier: \$TIER, model: \$(basename "\$MODEL"), RLM: \${SPOTLIGHT_RLM_OPENAI_BASE_URL:-off}).

Start or continue an investigation (one command per turn; answer each gate the same way):
  spotlight my-case "Investigate <target>: <what you want to know>"
  spotlight my-case "Approved, proceed."
  spotlight --stop        # stop the local model servers

Sessions are durable (harness/flue/data) — the same id resumes where it left off.
USAGE
  exit 0
fi
if [ "\${1:-}" = "--raw" ]; then shift; "\$FLUE" run spotlight "\$@"; exit \$?; fi
SESSION="\${1:?usage: spotlight-local <session-id> \"<message>\"}"
shift || true
case "\$SESSION" in
  ""|"."|".."|*[!A-Za-z0-9._-]*)
    echo "Invalid session id: use letters, numbers, dot, underscore, or hyphen (not . or ..)." >&2
    exit 2
    ;;
esac
CASE_ROOT="\$(expand_path "\${SPOTLIGHT_CASES_ROOT:-$SPOTLIGHT_CASES_ROOT}")"
CASE_DIR="\$CASE_ROOT/\$SESSION"
MSG="\${*:?usage: spotlight-local <session-id> \"<message>\"}"
MSG="\$MSG

Runtime case contract: use the exact project slug '\$SESSION' and exact CASE_DIR '\$CASE_DIR'. Do not derive a different slug or case path."
INPUT_JSON="\$(MSG="\$MSG" python3 -c 'import json,os; print(json.dumps({"message": os.environ["MSG"]}))')"

# Empty-turn guard: a small local orchestrator occasionally ends a long turn with an
# EMPTY final message (and then mirrors the empty on the next turn — grounded in the
# 2026-07-10 chain test). An orchestrator turn must never legitimately end empty (it
# always owes the user a gate presentation), so one automatic nudge is always safe
# and demonstrably breaks the mirror. tee keeps live streaming in the terminal.
OUT="\$("\$FLUE" run spotlight --id "\$SESSION" --input "\$INPUT_JSON" | tee /dev/stderr)"

# Safe report build. The model first authors data/report-draft.json (framing,
# priority, and prose); once all three structured inputs exist, code validates that
# plan and renders the final files. Human approval still governs publication.
if ! "\$SPOTLIGHT_PYTHON" "\$SPOTLIGHT_DIR/scripts/finalize-report.py" "\$CASE_DIR" --if-ready; then
  echo "Spotlight stopped: report finalization failed. Fix the named structured input; do not hand-edit generated HTML or Markdown." >&2
  exit 3
fi
if printf '%s\\n' "\$OUT" | tail -3 | grep -q '"text":""'; then
  echo "(empty final reply — auto-nudging once)" >&2
  NUDGE='{"message":"You returned an empty reply. Respond with text now: present your synthesis or status for the current gate, then stop and wait for approval."}'
  exec "\$FLUE" run spotlight --id "\$SESSION" --input "\$NUDGE"
fi
LAUNCHER_EOF
    chmod +x "$HOME/.local/bin/spotlight-local"
    printf "%s✓%s Launcher installed at ~/.local/bin/spotlight-local (llama.cpp + flue run spotlight)\n" "$_c_green" "$_c_reset"
  fi

# =====================================================================
# CLOUD MODE — pick a hosted runtime
# =====================================================================
else
  # SPOTLIGHT_RUNTIME ∈ {claude, codex, pi, opencode}
  case "$SPOTLIGHT_RUNTIME" in
    claude)   RT_BIN=claude;   RT_PKG="@anthropic-ai/claude-code"; RT_CTX="CLAUDE.md";  RT_NAME="Claude Code" ;;
    codex)    RT_BIN=codex;    RT_PKG="@openai/codex";             RT_CTX="";           RT_NAME="Codex" ;;
    pi)       RT_BIN=pi;       RT_PKG="@earendil-works/pi-coding-agent"; RT_CTX="";     RT_NAME="Pi" ;;
    opencode) RT_BIN=opencode; RT_PKG="opencode-ai";               RT_CTX="";           RT_NAME="OpenCode" ;;
    *) echo "Unknown SPOTLIGHT_RUNTIME: $SPOTLIGHT_RUNTIME" >&2; exit 1 ;;
  esac

  RT_LABEL="$RT_NAME"
  [ "$SPOTLIGHT_RUNTIME" = "opencode" ] && [ -n "$SPOTLIGHT_OPENCODE_PROVIDER" ] && \
    RT_LABEL="OpenCode (provider: $SPOTLIGHT_OPENCODE_PROVIDER)"

  step "$RT_LABEL"
  ensure_npm_global_exact "$RT_BIN" "$RT_PKG"
  if [ -n "$RT_CTX" ]; then
    run ln -sfn "$SPOTLIGHT_DIR/AGENTS.md" "$SPOTLIGHT_DIR/$RT_CTX"
    printf "%s✓%s AGENTS.md linked as %s\n" "$_c_green" "$_c_reset" "$RT_CTX"
  fi

  # Placement contract: every runtime shares the canonical store; Claude Code
  # additionally reads ~/.claude/skills, so it gets the adapter symlink.
  # Codex discovers skills via its project contract; Pi receives its native projection.
  step "Spotlight skills → canonical store"
  if [ "$SPOTLIGHT_RUNTIME" = "claude" ]; then
    link_spotlight_adapter "$HOME/.claude/skills/spotlight"
    [ "$DRY_RUN" = "1" ] || printf "%s✓%s Claude Code loads Spotlight skills via ~/.claude/skills/spotlight → %s\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_CANONICAL_SKILLS"
  else
    place_spotlight_skills_canonical
    [ "$DRY_RUN" = "1" ] || printf "%s✓%s Spotlight skills placed at %s\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_CANONICAL_SKILLS"
  fi
  if [ "$SPOTLIGHT_RUNTIME" = "pi" ]; then
    link_spotlight_adapter "$HOME/.pi/agent/skills/spotlight"
    [ "$DRY_RUN" = "1" ] || printf "%s✓%s Pi loads Spotlight skills via ~/.pi/agent/skills/spotlight → %s\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_CANONICAL_SKILLS"
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

step "SearXNG sovereign search"
provision_searxng

# Opt-in opsec (U7): the anonymized `fetch` (`--tor` / SPOTLIGHT_ANONYMIZE_FETCH)
# routes Crawl4AI through a local Tor SOCKS proxy on 9050 so scraping a target of
# investigation never reveals the operator's IP. Off by default; enable with
# SPOTLIGHT_TOR=1. Best-effort — a missing Tor only disables the anonymized path.
step "Tor (anonymized fetch, opt-in)"
if [ "${SPOTLIGHT_TOR:-0}" = "1" ]; then
  if command -v tor >/dev/null 2>&1; then
    printf "%s✓%s tor present (SOCKS 9050)\n" "$_c_green" "$_c_reset"
  elif [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: install tor (SOCKS 9050) for anonymized fetch\n'
  else
    ensure_tool tor || printf "%s→%s tor install skipped; --tor fetch unavailable until 'tor' is on PATH (SOCKS 9050)\n" "$_c_yellow" "$_c_reset"
  fi
else
  printf "%s→%s Tor not selected (SPOTLIGHT_TOR!=1); anonymized --tor fetch stays off\n" "$_c_yellow" "$_c_reset"
fi

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
  printf 'DRY-RUN: write .env with optional FIRECRAWL_API_KEY, %s + integration keys\n' "${SPOTLIGHT_CLOUD_KEY_VAR:-<none>}"
else
  cat > "$SPOTLIGHT_DIR/.env" <<'ENV_HEADER'
# Spotlight environment — generated by install-spotlight.sh
ENV_HEADER
  write_env_var SPOTLIGHT_DIR "$SPOTLIGHT_DIR"
  write_env_var SPOTLIGHT_VAULT_PATH "$SPOTLIGHT_VAULT_PATH"
  write_env_var SPOTLIGHT_WORKSPACE_PATH "$SPOTLIGHT_VAULT_PATH"
  write_env_var SPOTLIGHT_CASES_ROOT "$SPOTLIGHT_CASES_ROOT"
  write_env_var SPOTLIGHT_PYTHON "$SPOTLIGHT_DIR/.venv/bin/python"
  [ -n "$FIRECRAWL_API_KEY" ] && write_env_var FIRECRAWL_API_KEY "$FIRECRAWL_API_KEY"
  [ -n "$OSINT_NAV_API_KEY" ] && write_env_var OSINT_NAV_API_KEY "$OSINT_NAV_API_KEY"
  write_env_var SPOTLIGHT_NAVIGATOR_CONNECTION "$SPOTLIGHT_NAVIGATOR_CONNECTION"
  write_env_var SEARXNG_URL "$SEARXNG_URL"
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
    # Runtime model selection — the launcher reads these each run, so switching
    # 12b↔26b↔31b is an .env edit (path + tier), NOT a reinstall. The tier picks
    # the reasoning budget and the harness compaction profile.
    write_env_var SPOTLIGHT_MODEL_TIER "$SPOTLIGHT_MODEL_TIER"
    write_env_var SPOTLIGHT_GGUF_PATH "$HOME/Models/$MODEL_LEAF/$GGUF_FILE"
    # The launcher serves the RLM (fetch distillation + compaction summarizer) only
    # when this points at an existing GGUF; absent = graceful degradation.
    if [ -n "${SPOTLIGHT_RLM_GGUF_PATH:-}" ]; then
      write_env_var SPOTLIGHT_RLM_GGUF_PATH "$SPOTLIGHT_RLM_GGUF_PATH"
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
      printf "%s!%s Vault path changed from %s; initializing OpenKnowledge at the new vault — old vault data is not migrated.\n" \
        "$_c_yellow" "$_c_reset" "$PREV_VAULT_PATH"
    fi
  fi
  cat > "$SPOTLIGHT_DIR/.spotlight-config.json" <<CONFIG_EOF
{
  "search_library": "searxng",
  "vault_path": "$SPOTLIGHT_VAULT_PATH",
  "knowledge_destination": {
    "type": "openknowledge",
    "workspace_path": "$SPOTLIGHT_VAULT_PATH",
    "namespace": "spotlight",
    "project_id": "local:$SPOTLIGHT_VAULT_PATH",
    "destination_id": "destination:spotlight-local",
    "projection_namespace": "investigations",
    "story_namespace": "stories",
    "graph_database_path": "$SPOTLIGHT_VAULT_PATH/.knowledge-workspace/spotlight.sqlite",
    "provider_policy": {
      "provider_mode": "local_device",
      "model": "bge-m3:567m",
      "network_egress": "denied",
      "retention_days": 0
    },
    "legacy_mode": "off"
  },
  "case_workspace_root": "$SPOTLIGHT_CASES_ROOT",
  "cases_root": "$SPOTLIGHT_CASES_ROOT",
  "install_path": "$SPOTLIGHT_DIR",
  "mode": "$SPOTLIGHT_MODE",
  "model_tier": "$SPOTLIGHT_MODEL_TIER",
  "runtime": "$SPOTLIGHT_RUNTIME",
  "local_server": $([ -n "$SPOTLIGHT_LOCAL_SERVER" ] && printf '"%s"' "$SPOTLIGHT_LOCAL_SERVER" || echo null),
  "agent": $([ "$SPOTLIGHT_MODE" = "local" ] && printf '"flue"' || echo null),
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

step "OSINT tool index (local SQL — replaces the inline catalogue for tool discovery)"
if [ "$DRY_RUN" != "1" ]; then
  if command -v uv >/dev/null 2>&1; then
    _osint_build="uv run --quiet --with datasets --with pandas python3 scripts/osint-tools.py build"
  else
    _osint_build="python3 scripts/osint-tools.py build"
  fi
  if (cd "$SPOTLIGHT_DIR" && eval "$_osint_build"); then
    printf "OK    OSINT tool index built — 'osint-tools find' is ready\n"
  else
    printf "WARN  OSINT tool index not built (needs datasets+pandas). Run in %s:\n      %s\n" "$SPOTLIGHT_DIR" "$_osint_build"
  fi
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
run open-knowledge --cwd "$SPOTLIGHT_VAULT_PATH" init --mcp --local-only
if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: seal Spotlight route and initialize graph at %s/.knowledge-workspace/spotlight.sqlite\n' "$SPOTLIGHT_VAULT_PATH"
else
  mkdir -p "$SPOTLIGHT_VAULT_PATH/.knowledge-workspace"
  if [ ! -e "$SPOTLIGHT_VAULT_PATH/.knowledge-workspace/routes.json" ]; then
    cat > "$SPOTLIGHT_VAULT_PATH/.knowledge-workspace/routes.json" <<'ROUTES_EOF'
{"schema_version":"knowledge-routes/v1","routes":{"spotlight_verified":"spotlight"}}
ROUTES_EOF
    chmod 600 "$SPOTLIGHT_VAULT_PATH/.knowledge-workspace/routes.json"
  fi
  if [ ! -f "$SPOTLIGHT_VAULT_PATH/.knowledge-workspace/spotlight.sqlite" ]; then
    "$SPOTLIGHT_PYTHON" "$SPOTLIGHT_DIR/scripts/knowledge_destination.py" init-local \
      --workspace-root "$SPOTLIGHT_VAULT_PATH" \
      --db .knowledge-workspace/spotlight.sqlite \
      --destination-id destination:spotlight-local >/dev/null
  fi
  "$SPOTLIGHT_PYTHON" "$SPOTLIGHT_DIR/scripts/validate-install-config.py" \
    --config "$SPOTLIGHT_DIR/.spotlight-config.json" --initialize-local >/dev/null
fi
project_spotlight_skills_into_workspace
[ "$DRY_RUN" = "1" ] || printf "%s✓%s OpenKnowledge project and MCP registration configured\n" "$_c_green" "$_c_reset"

step "Spotlight command wrappers"
run mkdir -p "$HOME/.local/bin" "$SPOTLIGHT_DIR/.spotlight/logs"
if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: link %s/scripts/navigator-connect to ~/.local/bin/spotlight-navigator\n' "$SPOTLIGHT_DIR"
else
  chmod +x "$SPOTLIGHT_DIR/scripts/navigator-connect"
  ln -sfn "$SPOTLIGHT_DIR/scripts/navigator-connect" "$HOME/.local/bin/spotlight-navigator"
fi
if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: write spotlight-doctor and spotlight-update to ~/.local/bin\n'
else
  cat > "$HOME/.local/bin/spotlight-doctor" <<DOCTOR_EOF
#!/usr/bin/env bash
# Managed by the Spotlight public installer.
set -euo pipefail
export PATH="\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH"
expand_path() {
  local input="\$1"
  if [ "\$input" = "~" ]; then printf "%s\\n" "\$HOME"
  elif [[ "\$input" == "~/"* ]]; then printf "%s/%s\\n" "\$HOME" "\${input#\~/}"
  else printf "%s\\n" "\$input"; fi
}
SPOTLIGHT_DIR_DEFAULT_INPUT='$SPOTLIGHT_DIR_INPUT'
SPOTLIGHT_VAULT_INPUT='$SPOTLIGHT_VAULT_INPUT'
SPOTLIGHT_DIR_DEFAULT="\$(expand_path "\$SPOTLIGHT_DIR_DEFAULT_INPUT")"
SPOTLIGHT_DIR="\${SPOTLIGHT_DIR:-\$SPOTLIGHT_DIR_DEFAULT}"
SPOTLIGHT_VAULT_PATH="\${SPOTLIGHT_VAULT_PATH:-\$(expand_path "\$SPOTLIGHT_VAULT_INPUT")}"
SPOTLIGHT_CASES_ROOT="\${SPOTLIGHT_CASES_ROOT:-\$SPOTLIGHT_DIR/cases}"
SPOTLIGHT_PYTHON="\${SPOTLIGHT_PYTHON:-\$SPOTLIGHT_DIR/.venv/bin/python}"
export PATH="\$SPOTLIGHT_DIR/.venv/bin:\$PATH"
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
check_env_name SEARXNG_URL
check_path "\$SPOTLIGHT_VAULT_PATH" "Spotlight vault"
check_path "\$SPOTLIGHT_CASES_ROOT" "Spotlight case workspace"
check_cmd open-knowledge "OpenKnowledge CLI"
check_path "\$SPOTLIGHT_VAULT_PATH/.ok/config.yml" "OpenKnowledge project"
open-knowledge --cwd "\$SPOTLIGHT_VAULT_PATH" config validate >/dev/null 2>&1 && ok "OpenKnowledge config valid" || bad "OpenKnowledge config invalid"
"\$SPOTLIGHT_PYTHON" -c 'import crawl4ai, jsonschema, requests' >/dev/null 2>&1 && ok "Spotlight Python runtime" || bad "Spotlight Python runtime is incomplete"
DOCTOR_EOF
  if [ "$SPOTLIGHT_NAVIGATOR_CONNECTION" = "connected" ]; then
    echo 'check_cmd navigator "Navigator CLI"' >> "$HOME/.local/bin/spotlight-doctor"
    echo 'navigator auth status >/dev/null 2>&1 && ok "Navigator authenticated" || bad "Navigator authentication expired; reconnect from Spotlight setup"' >> "$HOME/.local/bin/spotlight-doctor"
  fi
  if [ -n "$FIRECRAWL_API_KEY" ]; then
    echo 'check_env_name FIRECRAWL_API_KEY' >> "$HOME/.local/bin/spotlight-doctor"
    echo 'check_cmd firecrawl "Firecrawl CLI"' >> "$HOME/.local/bin/spotlight-doctor"
  fi
  # Append runtime-specific checks
  case "$SPOTLIGHT_RUNTIME" in
    local)
      echo 'check_cmd llama-server "llama.cpp server"' >> "$HOME/.local/bin/spotlight-doctor"
      echo 'check_cmd node "Node.js (Flue harness)"' >> "$HOME/.local/bin/spotlight-doctor"
      echo 'check_path "$SPOTLIGHT_DIR/harness/flue/node_modules/.bin/flue" "Flue harness (npm install)"' >> "$HOME/.local/bin/spotlight-doctor"
      echo 'check_cmd spotlight-local "Spotlight local launcher"' >> "$HOME/.local/bin/spotlight-doctor"
      ;;
    claude)   echo 'check_cmd claude "Claude Code"' >> "$HOME/.local/bin/spotlight-doctor"; echo 'check_path "$SPOTLIGHT_DIR/CLAUDE.md" "Claude context link"' >> "$HOME/.local/bin/spotlight-doctor" ;;
    codex)    echo 'check_cmd codex "Codex"' >> "$HOME/.local/bin/spotlight-doctor" ;;
    pi)       echo 'check_cmd pi "Pi"' >> "$HOME/.local/bin/spotlight-doctor" ;;
    opencode) echo 'check_cmd opencode "OpenCode"' >> "$HOME/.local/bin/spotlight-doctor" ;;
  esac
  [ -n "$SPOTLIGHT_CLOUD_KEY_VAR" ] && echo "check_env_name $SPOTLIGHT_CLOUD_KEY_VAR" >> "$HOME/.local/bin/spotlight-doctor"
  [ "$SPOTLIGHT_INT_JUNKIPEDIA" = "true" ] && echo 'check_env_name JUNKIPEDIA_API_KEY' >> "$HOME/.local/bin/spotlight-doctor"
  [ "$SPOTLIGHT_INT_UNPAYWALL" = "true" ] && echo 'check_env_name UNPAYWALL_EMAIL' >> "$HOME/.local/bin/spotlight-doctor"
  [ "$SPOTLIGHT_INT_RLM" = "true" ] && echo 'check_env_name SPOTLIGHT_RLM_MODE' >> "$HOME/.local/bin/spotlight-doctor"
  cat >> "$HOME/.local/bin/spotlight-doctor" <<'DOCTOR_TAIL'
if [ -x "$SPOTLIGHT_DIR/integrations/preflight.py" ] || [ -f "$SPOTLIGHT_DIR/integrations/preflight.py" ]; then (cd "$SPOTLIGHT_DIR" && set -a && . .env && set +a && python3 integrations/preflight.py --text >/dev/null) && ok "Integration preflight" || bad "Integration preflight reported issues"; fi
if [ "$fail" -eq 0 ]; then printf "\nSpotlight doctor: OK\n"; else printf "\nSpotlight doctor: failed\n"; fi
exit "$fail"
DOCTOR_TAIL
  chmod +x "$HOME/.local/bin/spotlight-doctor"

  cat > "$HOME/.local/bin/spotlight-update" <<UPDATE_EOF
#!/usr/bin/env bash
# Managed by the Spotlight public installer.
set -euo pipefail
expand_path() {
  local input="\$1"
  if [ "\$input" = "~" ]; then printf "%s\\n" "\$HOME"
  elif [[ "\$input" == "~/"* ]]; then printf "%s/%s\\n" "\$HOME" "\${input#\~/}"
  else printf "%s\\n" "\$input"; fi
}
SPOTLIGHT_DIR_DEFAULT_INPUT='$SPOTLIGHT_DIR_INPUT'
SPOTLIGHT_DIR_DEFAULT="\$(expand_path "\$SPOTLIGHT_DIR_DEFAULT_INPUT")"
SPOTLIGHT_DIR="\${SPOTLIGHT_DIR:-\$SPOTLIGHT_DIR_DEFAULT}"
export PATH="\$SPOTLIGHT_DIR/.venv/bin:\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH"
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
public_channel="\$HOME/.local/share/buriedsignals/public-installer/spotlight"
[ -f "\$public_channel/bootstrap.sh" ] || { echo "signed public update channel is missing; re-run https://spotlight.buriedsignals.com/install-spotlight.sh once"; exit 1; }
bash "\$public_channel/bootstrap.sh" \
  --product spotlight \
  --release-base https://github.com/buriedsignals/spotlight/releases/latest/download \
  --runtime '$SPOTLIGHT_RUNTIME' \
  --product-root "\$SPOTLIGHT_DIR" \
  --action update \
  --no-handoff
after="\$(git -C "\$SPOTLIGHT_DIR" rev-parse HEAD)"
echo "Spotlight \$before -> \$after from the latest signed public bundle"
# Re-place skills per the placement contract: new manifest ids gain canonical
# links; the runtime adapters point at this directory and need no touch.
CANON='$SPOTLIGHT_CANONICAL_SKILLS'
mkdir -p "\$CANON"
if [ -s "\$SPOTLIGHT_DIR/skills.manifest" ]; then
  while IFS= read -r sid; do
    { [ -n "\$sid" ] && [ -d "\$SPOTLIGHT_DIR/skills/\$sid" ]; } || continue
    ln -sfn "\$SPOTLIGHT_DIR/skills/\$sid" "\$CANON/\$sid"
  done < "\$SPOTLIGHT_DIR/skills.manifest"
  echo "skills re-placed from skills.manifest"
fi
if "\$HOME/.local/bin/spotlight-doctor"; then
  echo "doctor passed"
else
  echo "doctor failed after update; rolling back to \$before"
  git reset --hard "\$before"
  python3 "\$public_channel/applicator.py" --action finalize \
    --bundle "\$public_channel/bundle.json" --signature "\$public_channel/bundle.sig" \
    --public-key "\$public_channel/public-installer-release.pub.pem" --runtime '$SPOTLIGHT_RUNTIME' \
    --receipt "\$HOME/.config/buriedsignals/public-installs/spotlight.json" || true
  exit 1
fi
python3 "\$public_channel/applicator.py" --action finalize \
  --bundle "\$public_channel/bundle.json" --signature "\$public_channel/bundle.sig" \
  --public-key "\$public_channel/public-installer-release.pub.pem" --runtime '$SPOTLIGHT_RUNTIME' \
  --receipt "\$HOME/.config/buriedsignals/public-installs/spotlight.json"
echo "update complete"
UPDATE_EOF
  chmod +x "$HOME/.local/bin/spotlight-update"
  if [ -f "$SPOTLIGHT_DIR/scripts/spotlight-uninstall" ]; then
    chmod +x "$SPOTLIGHT_DIR/scripts/spotlight-uninstall" || true
    ln -sfn "$SPOTLIGHT_DIR/scripts/spotlight-uninstall" "$HOME/.local/bin/spotlight-uninstall"
  fi
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
    codex)    LAUNCH_BIN="codex" ;;
    pi)       LAUNCH_BIN="pi" ;;
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
  export PATH="\$dir/.venv/bin:\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH"
  case "\${1:-}" in
    update)
      SPOTLIGHT_DIR="\$dir" "\$HOME/.local/bin/spotlight-update"
      return \$?
      ;;
    doctor)
      SPOTLIGHT_DIR="\$dir" "\$HOME/.local/bin/spotlight-doctor"
      return \$?
      ;;
    knowledge-retry)
      (cd "\$dir" && { [ -f .env ] && set -a && source .env && set +a; } && \
        "\${SPOTLIGHT_PYTHON:-\$dir/.venv/bin/python}" "\$dir/scripts/knowledge_projection.py" \
          --database "\${SPOTLIGHT_WORKSPACE_PATH}/.knowledge-workspace/spotlight.sqlite" \
          --root "\${SPOTLIGHT_WORKSPACE_PATH}" --cases-root "\${SPOTLIGHT_CASES_ROOT}" \
          --activation "\${SPOTLIGHT_WORKSPACE_PATH}/.knowledge-workspace/spotlight-activation.json" \
          --drain-mode operator)
      return \$?
      ;;
    uninstall)
      "\$HOME/.local/bin/spotlight-uninstall" "\${@:2}"
      return \$?
      ;;
    --help|-h|help)
      cat <<HELP
Usage: spotlight [subcommand | session-id "message"]

  (no arg)                  Launch the configured runtime (local: start the model
                            servers and print how to open a session)
  <session-id> "<message>"  Local runtime: send one investigation turn; re-run with
                            the same id to answer each gate. --stop stops the servers.
  update                    Apply the latest signed public release, then run doctor
  doctor                    Run the smoke test (structure, schemas, preflights)
  knowledge-retry           Retry pending local SQLite → Open Knowledge projections
  uninstall                 Remove owned files; preserve case data by default
  help                      This message
HELP
      return 0
      ;;
  esac
  # Projection recovery is local and bounded. A missing activation means the
  # graph-backed workflow is not enabled yet, so ordinary Spotlight startup
  # remains unchanged.
  (cd "\$dir" && { [ -f .env ] && set -a && source .env && set +a; } && \
    if [ -f "\${SPOTLIGHT_WORKSPACE_PATH}/.knowledge-workspace/spotlight-activation.json" ] && \
       [ -f "\${SPOTLIGHT_WORKSPACE_PATH}/.knowledge-workspace/spotlight.sqlite" ]; then
      "\${SPOTLIGHT_PYTHON:-\$dir/.venv/bin/python}" "\$dir/scripts/knowledge_projection.py" \
        --database "\${SPOTLIGHT_WORKSPACE_PATH}/.knowledge-workspace/spotlight.sqlite" \
        --root "\${SPOTLIGHT_WORKSPACE_PATH}" --cases-root "\${SPOTLIGHT_CASES_ROOT}" \
        --activation "\${SPOTLIGHT_WORKSPACE_PATH}/.knowledge-workspace/spotlight-activation.json" \
        --drain-mode startup || \
        echo "Spotlight projection recovery needs attention; run: spotlight knowledge-retry" >&2
    fi)
  (cd "\$dir" && { [ -f .env ] && set -a && source .env && set +a; } && $LAUNCH_BIN "\$@")
  local runtime_status=\$?
  if [ "$SPOTLIGHT_RUNTIME" != "local" ]; then
    # Frontier CLI exit backstop (not per-response middleware): agent instructions run
    # the same finalizer before presenting the report; this catches an omitted call when
    # the interactive process returns. API embedders must wire finalize-report.py into
    # their own response hook because an installer cannot intercept arbitrary API hosts.
    local active_case
    active_case="\$(python3 -c 'import json,sys; from pathlib import Path; d=Path(sys.argv[1]); p=d/".spotlight-config.json"; c=json.loads(p.read_text()) if p.is_file() else {}; r=Path(c.get("case_workspace_root") or c.get("cases_root") or "cases"); r=r if r.is_absolute() else d/r; a=c.get("active_project") or ""; print(r/a if a else "")' "\$dir" 2>/dev/null)"
    if [ -n "\$active_case" ]; then
      python3 "\$dir/scripts/finalize-report.py" "\$active_case" --if-ready || return 3
    fi
  fi
  return \$runtime_status
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
