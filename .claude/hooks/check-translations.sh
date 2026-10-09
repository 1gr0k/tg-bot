#!/bin/bash
# Stop: block finishing while Garage/Localizable.xcstrings has untranslated keys.
# Runs only if Garage/*.swift or the catalog changed since HEAD (or are new).
# Exit 2 feeds the key list back to Claude; stop_hook_active prevents a loop.
set -euo pipefail

input=$(cat)
[[ $(jq -r '.stop_hook_active // false' <<<"$input") == "true" ]] && exit 0

cd "${CLAUDE_PROJECT_DIR:-$PWD}"
catalog=Garage/Localizable.xcstrings
[[ -f "$catalog" ]] || exit 0

changed=$({
    git diff --name-only HEAD
    git ls-files --others --exclude-standard
} 2>/dev/null | grep -E '^Garage/(.*\.swift|Localizable\.xcstrings)$' || true)
[[ -n "$changed" ]] || exit 0

if ! out=$(scripts/check.sh --only strings 2>&1); then
    echo "$out" >&2
    exit 2
fi
