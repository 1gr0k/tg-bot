#!/bin/bash
# PostToolUse (Edit|Write): run swift-format on an edited .swift file.
# Uses the nearest .swift-format config; failures are reported but non-blocking.
set -euo pipefail

: "${DEVELOPER_DIR:=/Applications/Xcode-27.2.0-Beta.2.app/Contents/Developer}"
export DEVELOPER_DIR

file_path=$(jq -r '.tool_input.file_path // empty')
[[ "$file_path" == *.swift ]] || exit 0
[[ "$file_path" == /* ]] || file_path="$CLAUDE_PROJECT_DIR/$file_path"

case "$file_path" in
    "$CLAUDE_PROJECT_DIR"/*) ;;
    *) exit 0 ;;
esac

if ! output=$(swift format --in-place "$file_path" 2>&1); then
    echo "swift format failed for $file_path:" >&2
    echo "$output" >&2
    exit 1
fi
