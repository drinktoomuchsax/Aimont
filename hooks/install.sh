#!/usr/bin/env bash
# Install Aimont hooks for Claude Code.
#
# Copies emit.py to ~/.aimont/hooks/ and prints the hook config to add to
# your Claude Code settings. The argument only selects which settings-file
# path is named in that printed instruction (the file is not modified).
#
# Usage:
#   ./install.sh                      # references project .claude/settings.json
#   ./install.sh --global             # references ~/.claude/settings.json
#   ./install.sh <settings-path>      # references the given path
#   ./install.sh --global <path>      # --global still just selects ~/.claude

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EMIT_SCRIPT="$SCRIPT_DIR/emit.py"

if [ ! -f "$EMIT_SCRIPT" ]; then
    echo "error: emit.py not found next to install.sh ($EMIT_SCRIPT)" >&2
    exit 1
fi

if [ "${1:-}" = "--global" ]; then
    SETTINGS_FILE="$HOME/.claude/settings.json"
else
    # First positional arg (when not --global) is an optional settings path.
    SETTINGS_FILE="${1:-.claude/settings.json}"
fi

INSTALL_DIR="$HOME/.aimont/hooks"
mkdir -p "$INSTALL_DIR"
cp "$EMIT_SCRIPT" "$INSTALL_DIR/emit.py"
chmod +x "$INSTALL_DIR/emit.py"

echo "Emit script installed to: $INSTALL_DIR/emit.py"
echo ""
echo "Add the following hooks to your Claude Code settings ($SETTINGS_FILE)."
echo "See settings.template.json for the full configuration."
echo ""
echo "Or manually merge settings.template.json into your existing settings."
