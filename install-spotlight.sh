#!/usr/bin/env bash
# Spotlight installer — runs the same end-to-end install as the legacy
# setup.html generator, but as a static, version-controlled script.
#
# Driven by SPOTLIGHT_CONFIG: a base64-encoded block of `export KEY='value'`
# lines built by setup.html's buildExportBlock() helper. The block is decoded
# with `eval` — safety relies on the JS-side shellEscape() single-quoting
# every value before joining.
#
# Two delivery paths converge here:
#   curl -fsSL .../install-spotlight.sh | SPOTLIGHT_CONFIG='<b64>' bash
#   <generated .command file>  →  exports SPOTLIGHT_CONFIG then curls this.

set -euo pipefail

if [ -z "${SPOTLIGHT_CONFIG:-}" ]; then
  echo "SPOTLIGHT_CONFIG env var is required (base64-encoded shell export block)" >&2
  echo "Open setup.html in a browser to generate the install one-liner." >&2
  exit 1
fi

eval "$(printf '%s' "$SPOTLIGHT_CONFIG" | base64 -d)"

# Dry-run mode prints what would happen without touching the system.
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) ;;
  esac
done

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
SPOTLIGHT_CASES_ROOT="$SPOTLIGHT_VAULT_PATH/cases"
REPO_URL="https://github.com/buriedsignals/spotlight.git"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"

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
: "${SPOTLIGHT_INT_BROWSERUSE:=false}"
: "${SPOTLIGHT_INT_JUNKIPEDIA:=false}"
: "${JUNKIPEDIA_API_KEY:=}"
: "${SPOTLIGHT_INT_UNPAYWALL:=false}"
: "${UNPAYWALL_EMAIL:=}"
: "${FIRECRAWL_API_KEY:?firecrawl key missing from config}"
: "${OSINT_NAV_API_KEY:?osint-navigator key missing from config}"

# Derive model artifact names from the model selection.
case "$SPOTLIGHT_LOCAL_MODEL" in
  gemma)
    GGUF_FILE="gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
    OLLAMA_MODEL_DEFAULT="hf.co/unsloth/gemma-4-26B-A4B-it-GGUF:UD-Q4_K_M"
    OLLAMA_ALIAS_DEFAULT="spotlight-gemma4-q4"
    OLLAMA_SIZE_LABEL="~8 GB"
    ;;
  qwen27b)
    GGUF_FILE="Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf"
    OLLAMA_MODEL_DEFAULT="hf.co/HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive:IQ2_M"
    OLLAMA_ALIAS_DEFAULT="spotlight-qwen36-27b-q4"
    OLLAMA_SIZE_LABEL="~10 GB"
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
_c_reset=$'\033[0m'; _c_cyan=$'\033[36m'; _c_green=$'\033[32m'; _c_red=$'\033[31m'; _c_dim=$'\033[2m'; _c_bold=$'\033[1m'
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
  echo ""
  echo "Homebrew is needed to install other tools. Install it now? [Y/n]"
  read -r ans </dev/tty || ans="Y"
  if [[ "$ans" =~ ^[Nn] ]]; then echo "Aborted." >&2; exit 1; fi
  run /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
  [ -x /usr/local/bin/brew ] && eval "$(/usr/local/bin/brew shellenv)"
}

ensure_tool() {
  local cmd="$1"; local pkg="${2:-$1}"
  if command -v "$cmd" >/dev/null 2>&1; then
    printf "%s✓%s %s present\n" "$_c_green" "$_c_reset" "$cmd"; return 0
  fi
  ensure_brew
  spin "Installing $cmd via brew" brew install "$pkg"
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
      tmpdir="$(mktemp -d)"
      spin "Downloading Tolaria latest release" curl -L "https://github.com/refactoringhq/tolaria/releases/latest/download/Tolaria.app.tar.gz" -o "$tmpdir/Tolaria.app.tar.gz"
      run tar -xzf "$tmpdir/Tolaria.app.tar.gz" -C "$tmpdir"
      app_path="$(find "$tmpdir" -maxdepth 3 -name "Tolaria.app" -type d | head -1)"
      if [ -z "$app_path" ] && [ "$DRY_RUN" != "1" ]; then echo "Tolaria.app was not found in the release archive." >&2; exit 1; fi
      run mkdir -p "$HOME/Applications"
      [ -n "$app_path" ] && run cp -R "$app_path" "$HOME/Applications/Tolaria.app"
      run rm -rf "$tmpdir"
      printf "%s✓%s Tolaria.app installed in ~/Applications\n" "$_c_green" "$_c_reset"
    fi
    run open -a Tolaria 2>/dev/null || run open "$HOME/Applications/Tolaria.app" 2>/dev/null || true
  else
    echo "Tolaria selected. On Linux, install Tolaria from https://tolaria.md/; Spotlight will still write Markdown files to the vault path."
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
if ! command -v firecrawl >/dev/null 2>&1; then
  spin "Installing firecrawl-cli" npm install -g firecrawl-cli
else
  printf "%s✓%s firecrawl-cli already installed\n" "$_c_green" "$_c_reset"
fi

if ! command -v qmd >/dev/null 2>&1; then
  spin "Installing QMD vault search" npm install -g @tobilu/qmd
else
  printf "%s✓%s qmd already installed\n" "$_c_green" "$_c_reset"
fi

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
      spin "Downloading $GGUF_FILE from huggingface.co/$SPOTLIGHT_MODEL_REPO" \
        curl -L --fail --retry 3 --continue-at - \
          "https://huggingface.co/$SPOTLIGHT_MODEL_REPO/resolve/main/$GGUF_FILE" \
          -o "$MODEL_DIR/$GGUF_FILE"
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
    if ! command -v pi >/dev/null 2>&1; then
      spin "Installing @mariozechner/pi-coding-agent via npm" npm install -g @mariozechner/pi-coding-agent
    else
      printf "%s✓%s pi already installed\n" "$_c_green" "$_c_reset"
    fi

    step "pi-llama-cpp extension (model browser for llama-server)"
    spin "Installing pi-llama-cpp" pi install npm:pi-llama-cpp

    # Symlink Spotlight skills into pi's global skill dir.
    step "Spotlight skills → pi"
    run mkdir -p "$HOME/.pi/agent/skills"
    if [ "$DRY_RUN" = "1" ]; then
      printf 'DRY-RUN: symlink %s/skills/*/ into ~/.pi/agent/skills/\n' "$SPOTLIGHT_DIR"
    else
      for skill_dir in "$SPOTLIGHT_DIR/skills/"*/; do
        name=$(basename "$skill_dir")
        ln -sfn "$skill_dir" "$HOME/.pi/agent/skills/$name"
      done
      printf "%s✓%s pi loads %s Spotlight skills from ~/.pi/agent/skills/\n" "$_c_green" "$_c_reset" "$(ls -1 "$HOME/.pi/agent/skills" | wc -l | tr -d ' ')"
    fi

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
    run mkdir -p "$HOME/.config/opencode/skills"
    if [ "$DRY_RUN" = "1" ]; then
      printf 'DRY-RUN: symlink %s/skills/*/ into ~/.config/opencode/skills/\n' "$SPOTLIGHT_DIR"
    else
      for skill_dir in "$SPOTLIGHT_DIR/skills/"*/; do
        name=$(basename "$skill_dir")
        ln -sfn "$skill_dir" "$HOME/.config/opencode/skills/$name"
      done
      printf "%s✓%s opencode loads %s Spotlight skills from ~/.config/opencode/skills/\n" "$_c_green" "$_c_reset" "$(ls -1 "$HOME/.config/opencode/skills" | wc -l | tr -d ' ')"
    fi

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
        jq --arg base "$LOCAL_BASE_URL" --arg id "$OLLAMA_ALIAS_DEFAULT" --arg name "$MODEL_LEAF (local Ollama)" \
          '.provider["ollama"] = {"npm":"@ai-sdk/openai-compatible","name":"Ollama (local OpenAI-compatible)","options":{"baseURL":$base},"models":{($id):{"name":$name}}}' \
          "$OC_CFG" > "$TMP" && mv "$TMP" "$OC_CFG"
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
command -v pi           >/dev/null 2>&1 || { echo "pi missing — npm install -g @mariozechner/pi-coding-agent" >&2; exit 1; }
[ -f "\$MODEL" ] || { echo "Model not found: \$MODEL" >&2; exit 1; }
lsof -ti:8080 >/dev/null 2>&1 && { echo "Port 8080 already in use — kill the existing process first" >&2; exit 1; }
llama-server --model "\$MODEL" --alias qwen27 --host 127.0.0.1 --port 8080 \\
  --ctx-size 65536 --n-gpu-layers 999 --jinja --flash-attn on >/tmp/llama-server-pi.log 2>&1 &
SERVER_PID=\$!
cleanup() { kill -TERM "\$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM HUP
echo -n "Waiting for llama-server"
for i in {1..120}; do curl -sf http://127.0.0.1:8080/v1/models >/dev/null && { echo " ready."; break; }; echo -n "."; sleep 1; done
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
for i in {1..120}; do curl -sf http://127.0.0.1:8080/v1/models >/dev/null && { echo " ready."; break; }; echo -n "."; sleep 1; done
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
command -v pi     >/dev/null 2>&1 || { echo "pi missing — npm install -g @mariozechner/pi-coding-agent" >&2; exit 1; }
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
      spin "Installing $RT_PKG" npm install -g --prefix "$NPM_PREFIX" "$RT_PKG"
      export PATH="$NPM_PREFIX/bin:$PATH"
    else
      spin "Installing $RT_PKG" npm install -g "$RT_PKG"
    fi
  else
    printf "%s✓%s %s already installed\n" "$_c_green" "$_c_reset" "$RT_BIN"
  fi
  if [ -n "$RT_CTX" ]; then
    run ln -sfn "$SPOTLIGHT_DIR/AGENTS.md" "$SPOTLIGHT_DIR/$RT_CTX"
    printf "%s✓%s AGENTS.md linked as %s\n" "$_c_green" "$_c_reset" "$RT_CTX"
  fi
  if [ "$SPOTLIGHT_RUNTIME" = "opencode" ]; then
    printf "%s✓%s Provider key will be loaded from .env (%s)\n" "$_c_green" "$_c_reset" "$SPOTLIGHT_CLOUD_KEY_VAR"
    run mkdir -p "$HOME/.config/opencode/skills"
    if [ "$DRY_RUN" = "1" ]; then
      printf 'DRY-RUN: symlink %s/skills/*/ into ~/.config/opencode/skills/\n' "$SPOTLIGHT_DIR"
    else
      for skill_dir in "$SPOTLIGHT_DIR/skills/"*/; do
        name=$(basename "$skill_dir")
        ln -sfn "$skill_dir" "$HOME/.config/opencode/skills/$name"
      done
      printf "%s✓%s opencode loads Spotlight skills from %s/.config/opencode/skills/\n" "$_c_green" "$_c_reset" "$HOME"
    fi
  fi
fi

step "Python dependencies"
spin "Installing jsonschema + requests" bash -c "pip3 install --user --quiet jsonschema requests 2>/dev/null || pip install --user --quiet jsonschema requests"
if [ "$SPOTLIGHT_INT_BROWSERUSE" = "true" ]; then
  spin "Installing browser-use" bash -c "pip3 install --user --quiet browser-use 2>/dev/null || pip install --user --quiet browser-use"
  spin "Installing Chromium for browser-use" bash -c "python3 -m playwright install chromium 2>/dev/null || true"
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

step "Writing .spotlight-config.json"
if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: write %s/.spotlight-config.json\n' "$SPOTLIGHT_DIR"
else
  cat > "$SPOTLIGHT_DIR/.spotlight-config.json" <<CONFIG_EOF
{
  "search_library": "firecrawl",
  "vault_path": "$SPOTLIGHT_VAULT_PATH",
  "vault_type": "$([ "$SPOTLIGHT_VAULT_APP" = "tolaria" ] && echo tolaria || echo obsidian)",
  "vault_app": "$SPOTLIGHT_VAULT_APP",
  "cases_root": "$SPOTLIGHT_CASES_ROOT",
  "install_path": "$SPOTLIGHT_DIR",
  "mode": "$SPOTLIGHT_MODE",
  "runtime": "$SPOTLIGHT_RUNTIME",
  "local_server": $([ -n "$SPOTLIGHT_LOCAL_SERVER" ] && printf '"%s"' "$SPOTLIGHT_LOCAL_SERVER" || echo null),
  "agent": $([ "$SPOTLIGHT_MODE" = "local" ] && printf '"%s"' "$SPOTLIGHT_AGENT" || echo null),
  "opencode_provider": $([ -n "$SPOTLIGHT_OPENCODE_PROVIDER" ] && printf '"%s"' "$SPOTLIGHT_OPENCODE_PROVIDER" || echo null),
  "integrations": {
    "osint_navigator": true,
    "junkipedia": $SPOTLIGHT_INT_JUNKIPEDIA,
    "browser_use": $SPOTLIGHT_INT_BROWSERUSE,
    "unpaywall": $SPOTLIGHT_INT_UNPAYWALL
  },
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "last_used": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
CONFIG_EOF
fi

step "Vault scaffold"
run mkdir -p "$SPOTLIGHT_CASES_ROOT" "$SPOTLIGHT_VAULT_PATH/evidence" "$SPOTLIGHT_VAULT_PATH/captures" "$SPOTLIGHT_VAULT_PATH/briefs" "$SPOTLIGHT_VAULT_PATH/exports" "$SPOTLIGHT_VAULT_PATH/handoff-to-mycroft" "$SPOTLIGHT_VAULT_PATH/_schema"
if [ "$DRY_RUN" != "1" ] && [ ! -f "$SPOTLIGHT_VAULT_PATH/_index.md" ]; then
  cat > "$SPOTLIGHT_VAULT_PATH/_index.md" <<'INDEX_EOF'
---
type: spotlight-index
tags: [spotlight, index]
---
# Spotlight Vault

- cases/ — active investigations
- evidence/ — verified source material and citations
- captures/ — local page/document captures
- briefs/ — case summaries and handoffs
- exports/ — publishable packets and review artifacts
- handoff-to-mycroft/ — durable findings promoted to Mycroft
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
SPOTLIGHT_CASES_ROOT="\${SPOTLIGHT_CASES_ROOT:-\$SPOTLIGHT_VAULT_PATH/cases}"
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
check_path "\$SPOTLIGHT_CASES_ROOT" "Spotlight cases root"
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
