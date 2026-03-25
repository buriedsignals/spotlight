#!/usr/bin/env bash
# Pre-hook: block tool operations that access paths outside the workspace.
# Covers Bash, Read, Write, Edit, Glob, Grep.
# Uses Python os.path for canonicalization (macOS-compatible, defeats symlinks/traversal).
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_RAW="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE=$(python3 -c "import os; print(os.path.realpath('$WORKSPACE_RAW'))")

INPUT=$(cat)
TOOL=$(printf '%s' "$INPUT" | jq -r '.tool_name // ""')

deny() {
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$1"
    exit 0
}

# Resolve path using Python (handles symlinks, .., missing paths on macOS)
resolve_path() {
    python3 -c "import os, sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))" "$1"
}

check_path() {
    local raw_path="$1"
    local resolved
    resolved=$(resolve_path "$raw_path")

    # Allow workspace
    [[ "$resolved" == "$WORKSPACE"* ]] && return 0
    # Allow intelligence vault (symlink target)
    INTELLIGENCE_TARGET=$(python3 -c "import os; print(os.path.realpath('$WORKSPACE_RAW/intelligence'))" 2>/dev/null || echo "")
    [[ -n "$INTELLIGENCE_TARGET" && "$resolved" == "$INTELLIGENCE_TARGET"* ]] && return 0
    # Allow /tmp (macOS resolves to /private/tmp)
    [[ "$resolved" == /tmp* || "$resolved" == /private/tmp* || "$resolved" == /var/tmp* || "$resolved" == /private/var/tmp* ]] && return 0
    # Allow /dev/null and friends
    [[ "$resolved" == /dev/null || "$resolved" == /dev/stdin || "$resolved" == /dev/stdout || "$resolved" == /dev/stderr ]] && return 0

    # For Bash tool: allow system binary/lib directories (realpath already resolved traversal)
    if [[ "$TOOL" == "Bash" ]]; then
        for prefix in /usr/bin /usr/local/bin /opt/homebrew /bin /usr/lib /usr/local/lib /usr/share /Library /Applications; do
            [[ "$resolved" == "$prefix"* ]] && return 0
        done
    fi

    deny "Blocked: path $raw_path resolves to $resolved which is outside workspace"
}

case "$TOOL" in
    Bash)
        COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // ""')
        [[ -z "$COMMAND" ]] && exit 0

        # Block eval/source that defer path resolution
        if printf '%s' "$COMMAND" | grep -qE '\beval\b|\bsource\b'; then
            deny "Blocked: eval/source can defer path resolution"
        fi

        # Block symlink creation
        if printf '%s' "$COMMAND" | grep -qE '\bln\b.*-s'; then
            deny "Blocked: symlink creation could escape workspace"
        fi

        # Block tilde expansion to other users
        if printf '%s' "$COMMAND" | grep -qE '~[a-zA-Z]'; then
            deny "Blocked: tilde expansion to other user home directory"
        fi

        # Check $HOME references
        if printf '%s' "$COMMAND" | grep -qE '\$HOME|\$\{HOME\}'; then
            home_paths=$(printf '%s' "$COMMAND" | grep -oE '\$\{?HOME\}?/[^ ;|&"'"'"']*' || true)
            if [[ -n "$home_paths" ]]; then
                while IFS= read -r hp; do
                    expanded="${hp/\$\{HOME\}/$HOME}"
                    expanded="${expanded/\$HOME/$HOME}"
                    check_path "$expanded"
                done <<< "$home_paths"
            fi
        fi

        # Extract and check all absolute paths from the command
        abs_paths=$(printf '%s' "$COMMAND" | grep -oE '/[a-zA-Z0-9_.~@/-]+' || true)
        if [[ -n "$abs_paths" ]]; then
            while IFS= read -r p; do
                [[ -n "$p" ]] && check_path "$p"
            done <<< "$abs_paths"
        fi
        ;;
    Read)
        FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // ""')
        [[ -n "$FILE_PATH" ]] && check_path "$FILE_PATH"
        ;;
    Write)
        FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // ""')
        [[ -n "$FILE_PATH" ]] && check_path "$FILE_PATH"
        ;;
    Edit)
        FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // ""')
        [[ -n "$FILE_PATH" ]] && check_path "$FILE_PATH"
        ;;
    Glob)
        DIR_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.path // ""')
        [[ -n "$DIR_PATH" ]] && check_path "$DIR_PATH"
        ;;
    Grep)
        DIR_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.path // ""')
        [[ -n "$DIR_PATH" ]] && check_path "$DIR_PATH"
        ;;
esac

exit 0
