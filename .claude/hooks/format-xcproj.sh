#!/bin/bash
# PostToolUse (Edit|Write): canonicalize project.xcproj after it is edited.
# Exit 2 sends xcprojformatter's error back to Claude.
set -euo pipefail

: "${DEVELOPER_DIR:=/Applications/Xcode-27.2.0-Beta.2.app/Contents/Developer}"
export DEVELOPER_DIR

file_path=$(jq -r '.tool_input.file_path // empty')
[[ "$file_path" == *project.xcproj ]] || exit 0
[[ "$file_path" == /* ]] || file_path="$CLAUDE_PROJECT_DIR/$file_path"

case "$file_path" in
    "$CLAUDE_PROJECT_DIR"/*) ;;
    *) exit 0 ;;
esac

project_dir=$(dirname "$file_path")

if ! output=$("$DEVELOPER_DIR/usr/bin/xcprojformatter" --update "$project_dir" 2>&1); then
    echo "xcprojformatter --update failed for $project_dir:" >&2
    # Drop the usage text that follows the "-----" separator.
    awk '/^-+$/ { exit } NF' <<<"$output" >&2
    exit 2
fi
