#!/bin/bash
# PostToolUse (Edit|Write): `ruff format`, затем `ruff check --fix` для отредактированного *.py
# внутри проекта (через `uv run`, версия ruff — из uv.lock).
#
# F401 (неиспользуемый импорт) не чинится автоматически (`--unfixable F401`): иначе хук стирает
# импорт между двумя правками, когда агент добавил его одним Edit, а использует следующим.
# Такой импорт остаётся замечанием: агент увидит его и либо использует, либо удалит сам.
#
# Exit 2 — синтаксическая ошибка, оставшиеся замечания или недоступный ruff; stderr уходит Claude.
# Кейсы — scripts/test-hooks.sh.
set -euo pipefail

input=$(cat)
file_path=$(jq -r '.tool_input.file_path // empty' <<<"$input")
[[ "$file_path" == *.py ]] || exit 0

project=${CLAUDE_PROJECT_DIR:-$PWD}
[[ "$file_path" == /* ]] || file_path="$project/$file_path"
[[ -f "$file_path" ]] || exit 0

# Физические пути: `..` и симлинки (/tmp → /private/tmp) не обманывают проверку «внутри проекта».
project=$(cd "$project" && pwd -P)
file_dir=$(cd "$(dirname "$file_path")" && pwd -P)
file_path=$file_dir/$(basename "$file_path")
case "$file_path" in
    "$project"/*) ;;
    *) exit 0 ;;
esac

cd "$project"
if ! command -v uv >/dev/null 2>&1; then
    echo "format-python: uv не найден в PATH — ruff не запущен, $file_path не проверен." >&2
    exit 2
fi
if ! version=$(uv run --frozen ruff --version 2>&1); then
    echo "format-python: ruff недоступен через \`uv run\` (нужен \`uv sync\`?) — $file_path не проверен:" >&2
    echo "$version" >&2
    exit 2
fi

# --force-exclude: файлы из exclude ruff (например, .venv) пропускаются и при явном пути.
if ! output=$(uv run --frozen ruff format --force-exclude -- "$file_path" 2>&1); then
    echo "ruff format failed for $file_path:" >&2
    echo "$output" >&2
    exit 2
fi
if ! output=$(uv run --frozen ruff check --fix --unfixable F401 --force-exclude \
    --output-format concise -- "$file_path" 2>&1); then
    echo "ruff check: remaining issues in $file_path:" >&2
    echo "$output" >&2
    exit 2
fi
