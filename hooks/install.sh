#!/usr/bin/env bash
# Install Aimont hooks into Claude Code settings.
# Usage: ./install.sh [--global]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EMIT_SCRIPT="$SCRIPT_DIR/emit.py"

if [ "$1" = "--global" ]; then
    SETTINGS_FILE="$HOME/.claude/settings.json"
else
    SETTINGS_FILE="${2:-.claude/settings.json}"
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
